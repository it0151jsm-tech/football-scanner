"""Opt-in image extraction uses tiny generated fixtures and mocks, never paid HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import traceback
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import requests

from stats_image_reader import prepare_images, read_images, validate_result
from stats_quality import canonical_name, missing_fields, validate_stats


class Upload:
    def __init__(self, data, mime='image/png', name='stats.png'):
        self.data, self.type, self.name, self.size = data, mime, name, len(data)

    def getvalue(self):
        return self.data


def image(fmt='PNG', color='white', size=(3, 2)):
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[fmt]
    return Upload(buffer.getvalue(), mime, 'stats.' + fmt.lower())


def draft():
    return dict(home_team='Synthetic Home', away_team='Synthetic Away', home_goals=2,
                away_goals=1, stats={'xG': {'home': 1.8, 'away': .7},
                                    'shots': {'home': 21, 'away': 10},
                                    'shots_on_target': {'home': 10, 'away': 3}}, warnings=[])


def response(raw=None, content=None):
    result = Mock()
    result.content = b'bounded mock response'
    result.json.return_value = {'choices': [{'message': {'content': content if content is not None else json.dumps(raw or draft())}}]}
    return result


class StatsImageReaderTests(unittest.TestCase):
    def setUp(self):
        self.no_network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def test_one_through_six_real_decodable_images_are_prepared_offline(self):
        for count in range(1, 7):
            with self.subTest(count=count):
                prepared = prepare_images([image() for _ in range(count)])
                self.assertEqual(len(prepared['images']), count)
                self.assertEqual(len(prepared['content']), count)
                self.assertGreater(prepared['total_bytes'], 0)
                self.assertTrue(all(item['width'] == 3 and item['height'] == 2 for item in prepared['images']))

    def test_all_three_declared_formats_must_match_real_content(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(fmt=fmt):
                prepared = prepare_images([image(fmt)])
                self.assertEqual(prepared['images'][0]['mime'], image(fmt).type)
        forged = image()
        forged.type = 'image/jpeg'
        with self.assertRaises(ValueError):
            prepare_images([forged])

    def test_empty_fake_truncated_and_unsupported_images_are_rejected(self):
        for uploaded in (Upload(b''), Upload(b'not an image'), Upload(image().data[:30]),
                         Upload(image().data, 'text/plain')):
            with self.subTest(size=uploaded.size, mime=uploaded.type):
                with self.assertRaises(ValueError):
                    prepare_images([uploaded])

    def test_count_and_per_image_byte_limits_are_enforced_before_sending(self):
        for files in ([], [image()] * 7, None, 'stats.png'):
            with self.subTest(kind=type(files).__name__):
                with self.assertRaises(ValueError):
                    prepare_images(files)
        uploaded = image()
        uploaded.size = 4_000_001
        uploaded.getvalue = Mock(side_effect=AssertionError('Oversized declared upload must not be read'))
        with self.assertRaises(ValueError):
            prepare_images([uploaded])
        uploaded.getvalue.assert_not_called()
        with self.assertRaises(ValueError):
            prepare_images([Upload(b'x' * 4_000_001)])

    def test_total_byte_and_decoded_dimensions_are_bounded(self):
        uploaded = image()
        with patch('stats_image_reader.MAX_TOTAL_IMAGE_BYTES', uploaded.size * 2 - 1):
            with self.assertRaises(ValueError):
                prepare_images([uploaded, uploaded])
        with self.assertRaises(ValueError):
            prepare_images([image(size=(10_001, 1))])
        with patch('stats_image_reader.MAX_IMAGE_PIXELS', 5):
            with self.assertRaises(ValueError):
                prepare_images([uploaded])

    def test_animated_image_is_not_silently_reduced_to_first_frame(self):
        buffer = BytesIO()
        first, second = Image.new('RGB', (2, 2), 'red'), Image.new('RGB', (2, 2), 'blue')
        first.save(buffer, format='WEBP', save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaises(ValueError):
            prepare_images([Upload(buffer.getvalue(), 'image/webp')])

    def test_metadata_uses_actual_content_and_actual_aware_receipt(self):
        uploaded = image()
        uploaded.name = '../../private\\stats.png'
        before = datetime.now(timezone.utc)
        prepared = prepare_images([uploaded])
        after = datetime.now(timezone.utc)
        metadata = prepared['images'][0]
        received = datetime.fromisoformat(metadata['received_at'])
        self.assertLessEqual(before, received)
        self.assertLessEqual(received, after)
        self.assertEqual(metadata['filename'], 'stats.png')
        self.assertEqual(metadata['sha256'], hashlib.sha256(uploaded.data).hexdigest())
        self.assertEqual(metadata['byte_count'], len(uploaded.data))
        self.assertEqual(metadata['received_at'], prepared['received_at'])

    def test_batch_fingerprint_is_stable_for_same_content_not_clock_order_or_filename(self):
        white, red = image(), image(color='red')
        one = prepare_images([white, red])
        white.name = 'renamed.png'
        two = prepare_images([red, white])
        self.assertEqual(one['batch_fingerprint'], two['batch_fingerprint'])
        self.assertNotEqual(one['batch_fingerprint'], prepare_images([red])['batch_fingerprint'])
        self.assertNotEqual(prepare_images([white])['batch_fingerprint'], prepare_images([white, white])['batch_fingerprint'])

    def test_explicit_user_request_and_valid_key_are_required_without_http(self):
        send = Mock()
        with self.assertRaises(ValueError):
            read_images([image()], 'offline-key', send=send)
        for token in (None, '', ' ', True, 'secret\r\nheader'):
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaises(ValueError):
                    read_images([image()], token, send=send, user_requested=True)
        send.assert_not_called()

    def test_request_is_once_only_and_result_is_always_a_review_draft(self):
        send = Mock(return_value=response())
        with patch('personal_stats.save_verified') as save:
            result = read_images([image()], 'offline-fixture-key', send, user_requested=True)
            save.assert_not_called()
        self.assertEqual(send.call_count, 1)
        request = send.call_args
        self.assertEqual(request.args[0], 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(request.kwargs['headers'], {'Authorization': 'Bearer offline-fixture-key'})
        self.assertEqual(request.kwargs['json']['model'], 'gpt-4o-mini')
        self.assertEqual(len(request.kwargs['json']['messages'][0]['content']), 2)
        self.assertFalse(result['verified'])
        self.assertTrue(result['review_required'])
        self.assertEqual(result['stats']['xG']['home'], 1.8)
        self.assertEqual(len(result['extraction']['images']), 1)
        self.assertEqual(result['extraction']['provider'], 'OpenAI')
        public_metadata = json.dumps(result)
        self.assertNotIn('offline-fixture-key', public_metadata)
        self.assertNotIn('data:image', public_metadata)
        self.assertNotIn('Authorization', public_metadata)

    def test_default_transport_can_be_mocked_without_captured_real_post(self):
        with patch('stats_image_reader.requests.post', return_value=response()) as send:
            read_images([image()], 'offline-key', user_requested=True)
        send.assert_called_once()

    def test_provider_exception_and_error_body_never_expose_secrets(self):
        for failure in ('send', 'status', 'json'):
            with self.subTest(failure=failure):
                provider = response()
                error = requests.HTTPError('Bearer secret-fixture-token; private provider body; ?apiKey=secret')
                send = Mock(return_value=provider)
                if failure == 'send':
                    send.side_effect = error
                elif failure == 'status':
                    provider.raise_for_status.side_effect = error
                else:
                    provider.json.side_effect = error
                token = 'secret-fixture-token'
                try:
                    read_images([image()], token, send, user_requested=True)
                except ValueError as exc:
                    visible = ''.join(traceback.format_exception(exc))
                    self.assertNotIn('secret-fixture-token', visible)
                    self.assertNotIn('private provider body', visible)
                    self.assertNotIn('apiKey=', visible)
                    self.assertIn('Image extraction failed', str(exc))
                else:
                    self.fail('Expected sanitized failure')
                self.assertEqual(send.call_count, 1)

    def test_response_size_shape_duplicate_json_and_nan_are_fail_closed(self):
        malformed = [response(content='not JSON'), response(content='[]'),
                     response(content=json.dumps(draft()).replace('"home_goals": 2', '"home_goals": 2, "home_goals": 3')),
                     response(content=json.dumps(draft()).replace('1.8', 'NaN'))]
        oversized = response()
        oversized.content = b'x' * 100_001
        malformed.append(oversized)
        for provider in malformed:
            with self.subTest(provider=provider):
                send = Mock(return_value=provider)
                with self.assertRaisesRegex(ValueError, 'Image extraction failed'):
                    read_images([image()], 'offline-key', send, user_requested=True)
                self.assertEqual(send.call_count, 1)

    def test_provider_cannot_claim_verified_or_return_arbitrary_schema(self):
        for mutate in (lambda value: value.update(verified=True),
                       lambda value: value.pop('stats'),
                       lambda value: value.update(stats=[]),
                       lambda value: value.update(unexpected='private payload')):
            value = draft()
            mutate(value)
            with self.assertRaises(ValueError):
                validate_result(value)

    def test_nullable_identity_and_scores_never_become_invented_zeroes(self):
        value = draft()
        value.update(home_team=None, away_team=None, home_goals=None, away_goals=None, stats={})
        result = validate_result(value)
        self.assertIsNone(result['home_team'])
        self.assertIsNone(result['home_goals'])
        self.assertEqual(len(result['data_quality']['missing_fields']), 6)
        self.assertTrue(any('0-0' in warning for warning in result['warnings']))
        value.update(home_goals=2)
        result = validate_result(value)
        self.assertIsNone(result['home_goals'])
        self.assertIsNone(result['away_goals'])
        self.assertTrue(any('Incomplete score' in warning for warning in result['warnings']))

    def test_identity_score_and_warning_types_are_strict(self):
        for field, values in {'home_team': [[], 1, '', 'bad\nname', 'x' * 201],
                              'home_goals': [True, 2.0, '2', -1, 101],
                              'warnings': [None, 'warning', [None], [{}], ['x' * 501], ['x'] * 41]}.items():
            for invalid in values:
                with self.subTest(field=field, value_type=type(invalid).__name__):
                    value = draft()
                    value[field] = invalid
                    with self.assertRaises(ValueError):
                        validate_result(value)
        value = draft()
        value['away_team'] = value['home_team'].upper()
        with self.assertRaises(ValueError):
            validate_result(value)

    def test_metric_aliases_normalize_without_creating_xg_from_goals(self):
        value = draft()
        value['stats'] = {'Expected goals (xG)': {'home': 1.8, 'away': .7},
                          'Total shots': {'home': 21, 'away': 10},
                          'Shots on target': {'home': 10, 'away': 3},
                          'Ball possession': {'home': 48, 'away': 52}}
        result = validate_result(value)
        self.assertEqual(set(result['stats']), {'xG', 'shots', 'shots_on_target', 'possession'})
        self.assertEqual(missing_fields(result['stats']), [])
        self.assertEqual(canonical_name('ยิงเข้ากรอบ'), 'shots_on_target')
        for unknown in ('goals', 'A score', 'EV', 'confidence'):
            with self.subTest(metric=unknown):
                with self.assertRaises(ValueError):
                    validate_stats({unknown: {'home': 2, 'away': 1}})

    def test_numbers_ranges_and_home_away_schema_are_strict(self):
        for metric, invalid in [('xG', '1.8'), ('xG', True), ('xG', float('nan')),
                                ('xG', float('inf')), ('xG', 10 ** 400), ('xG', -1),
                                ('xG', 101), ('possession', 101), ('shots', 1.5),
                                ('shots', 201), ('red_cards', 23)]:
            with self.subTest(metric=metric, value_type=type(invalid).__name__):
                with self.assertRaises(ValueError):
                    validate_stats({metric: {'home': invalid, 'away': None}})
        with self.assertRaises(ValueError):
            validate_stats({'xG': {'home': 1, 'away': 1, 'verified': True}})
        self.assertEqual(validate_stats({'shots': {'home': 3.0}}), {'shots': {'home': 3, 'away': None}})

    def test_alias_collision_is_strict_for_save_and_quarantined_in_image_draft(self):
        value = draft()
        value['stats']['Expected goals'] = {'home': 3.0, 'away': .7}
        with self.assertRaises(ValueError):
            validate_stats(value['stats'])
        result = validate_result(value)
        self.assertIsNone(result['stats']['xG']['home'])
        self.assertEqual(result['stats']['xG']['away'], .7)
        self.assertTrue(result['data_quality']['has_conflicts'])
        self.assertTrue(any('Conflicting source' in warning for warning in result['warnings']))
        value['stats']['x_g'] = {'home': 1.8, 'away': .7}
        self.assertIsNone(validate_result(value)['stats']['xG']['home'])

    def test_reported_conflicting_images_cannot_leave_arbitrarily_selected_numbers(self):
        value = draft()
        value['warnings'] = ['Conflicting xG values across the two sources']
        result = validate_result(value)
        self.assertTrue(result['data_quality']['has_conflicts'])
        self.assertTrue(all(pair == {'home': None, 'away': None} for pair in result['stats'].values()))
        self.assertIsNone(result['home_goals'])
        self.assertIsNone(result['away_goals'])

    def test_inconsistent_counts_possession_and_inverse_xga_are_quarantined(self):
        for additions, affected in [({'shots_on_target': {'home': 22, 'away': 3}}, [('shots_on_target', 'home')]),
                                    ({'possession': {'home': 30, 'away': 40}}, [('possession', 'home'), ('possession', 'away')]),
                                    ({'xGA': {'home': 2.0, 'away': 1.8}}, [('xGA', 'home'), ('xG', 'away')]),
                                    ({'passes': {'home': 100, 'away': 90}, 'accurate_passes': {'home': 101, 'away': 80}}, [('accurate_passes', 'home')])]:
            with self.subTest(affected=affected):
                value = draft()
                value['stats'].update(additions)
                with self.assertRaises(ValueError):
                    validate_stats(value['stats'])
                result = validate_result(value)
                for metric, side in affected:
                    self.assertIsNone(result['stats'][metric][side])
                self.assertTrue(result['data_quality']['has_conflicts'])

    def test_consistent_aliases_and_rounded_possession_are_retained_without_averaging(self):
        stats = {'xG': {'home': 1.8, 'away': .7}, 'Expected goals': {'home': 1.8, 'away': None},
                 'possession': {'home': 48, 'away': 51}}
        result = validate_stats(stats)
        self.assertEqual(result['xG'], {'home': 1.8, 'away': .7})
        self.assertEqual(result['possession'], {'home': 48, 'away': 51})


if __name__ == '__main__':
    unittest.main()
