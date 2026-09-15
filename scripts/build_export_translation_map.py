"""Build an optional local English display cache using a loopback Ollama model.

The script reads a previously exported readable JSON file, translates only
non-evidence strings that still contain Chinese, and writes a resumable local
cache. Source-document evidence text is never translated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


CJK = re.compile(r'[\u3400-\u9fff]')
GENERIC_TRANSLATION = re.compile(
    r'\b(?:this|the) input contains (?:a )?visual description of a drawing page\b', re.I
)
SKIP_FIELDS = {'text', 'evidence_text'}
NUMBER_TOKEN = re.compile(r'\d+(?:[./]\d+)*')
MONTH_NUMBERS = {
    'january': '1', 'february': '2', 'march': '3', 'april': '4', 'may': '5', 'june': '6',
    'july': '7', 'august': '8', 'september': '9', 'october': '10', 'november': '11', 'december': '12',
}
WORD_NUMBERS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
    'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10', 'eleven': '11',
    'twelve': '12', 'thirteen': '13', 'fourteen': '14', 'fifteen': '15', 'sixteen': '16',
    'seventeen': '17', 'eighteen': '18', 'nineteen': '19', 'twenty': '20',
}
ORDINAL_NUMBERS = {
    'first': '1', 'second': '2', 'third': '3', 'fourth': '4', 'fifth': '5',
    'sixth': '6', 'seventh': '7', 'eighth': '8', 'ninth': '9', 'tenth': '10',
    'eleventh': '11', 'twelfth': '12', 'thirteenth': '13', 'fourteenth': '14',
    'fifteenth': '15', 'sixteenth': '16', 'seventeenth': '17', 'eighteenth': '18',
    'nineteenth': '19', 'twentieth': '20',
}


def number_tokens(value: str) -> set[str]:
    tokens = NUMBER_TOKEN.findall(value)
    lowered = value.casefold()
    tokens.extend(number for month, number in MONTH_NUMBERS.items() if month in lowered)
    tokens.extend(number for word, number in WORD_NUMBERS.items()
                  if re.search(rf'\b{word}\b', lowered))
    tokens.extend(number for word, number in ORDINAL_NUMBERS.items()
                  if re.search(rf'\b{word}\b', lowered))
    return set(tokens)


def translation_is_safe(source: str, translation: str) -> bool:
    if not translation or CJK.search(translation) or GENERIC_TRANSLATION.search(translation):
        return False
    return not (number_tokens(source) - number_tokens(translation))


def suspicious_collisions(values: dict[str, str]) -> set[str]:
    reverse = defaultdict(list)
    for source, translation in values.items():
        if translation and not CJK.search(translation):
            reverse[re.sub(r'\s+', ' ', translation).strip().casefold()].append(source)
    return {translation for translation, sources in reverse.items()
            if len(sources) >= 3 and len(translation) >= 80}


def clean_translations(values: dict[str, str]) -> tuple[dict[str, str], int]:
    collisions = suspicious_collisions(values)
    clean = {
        source: translation for source, translation in values.items()
        if translation_is_safe(source, translation)
        and re.sub(r'\s+', ' ', translation).strip().casefold() not in collisions
    }
    return clean, len(values) - len(clean)


def strings_to_translate(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from strings_to_translate(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings_to_translate(item, path + (index,))
    elif isinstance(value, str) and (not path or path[-1] not in SKIP_FIELDS) and CJK.search(value):
        yield value


def batches(values: list[str], max_items=1, max_chars=5000):
    batch = []; size = 0
    for value in values:
        if batch and (len(batch) >= max_items or size + len(value) > max_chars):
            yield batch; batch = []; size = 0
        batch.append(value); size += len(value)
    if batch:
        yield batch


def translate(client: httpx.Client, endpoint: str, model: str, values: list[str], strict=False) -> list[str]:
    long_input = any(len(value) > 1000 for value in values)
    system_text = (
        'Translate every Chinese construction, engineering, material, equipment, inspection, and status phrase '
        'into concise professional English. Preserve all numbers, units, model numbers, standards, tags, and '
        'proper names exactly. Translate mixed Chinese-English text into natural English. Every CJK sequence '
        'is source language, not a proper name: translate it fully and never copy a Chinese character. Do not summarize, '
        'add facts, use Markdown, or include commentary. Return exactly one translation for every input index. '
        + ('For long diagnostic prose, remove repetition while retaining every distinct fact, action, number, '
           'standard, and named item; keep the result under 600 English words. ' if long_input else '')
        + ('Your previous result was invalid. The text field must contain English words only and retain every '
           'distinct number.' if strict else '')
    )
    schema = {
        'type': 'object',
        'properties': {'translations': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'index': {'type': 'integer'}, 'text': {'type': 'string'}},
            'required': ['index', 'text'], 'additionalProperties': False,
        }}},
        'required': ['translations'], 'additionalProperties': False,
    }
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_text},
            {'role': 'user', 'content': json.dumps({'items': [
                {'index': index, 'text': value} for index, value in enumerate(values)
            ]}, ensure_ascii=False)},
        ],
        'stream': False,
        'think': False,
        'format': schema,
        'options': {
            'temperature': 0,
            'num_predict': (3072 if long_input else
                            min(4096, max(1024, sum(len(value) for value in values) * 2 + 512))),
        },
    }
    if len(values) == 1:
        payload['messages'][1]['content'] = 'Translate this source text fully. Output only the English text:\n' + values[0]
        payload.pop('format')
    response = client.post(endpoint.rstrip('/') + '/api/chat', json=payload)
    response.raise_for_status()
    content = response.json()['message']['content']
    if len(values) == 1:
        translations = [re.sub(r'\s+', ' ', content).strip().strip('"')]
    else:
        result = json.loads(content)['translations']
        by_index = {item['index']: re.sub(r'\s+', ' ', item['text']).strip() for item in result}
        if set(by_index) != set(range(len(values))):
            raise ValueError('The local translator returned an incomplete index set.')
        translations = [by_index[index] for index in range(len(values))]
    invalid = [index for index, (source, translation) in enumerate(zip(values, translations))
               if not translation_is_safe(source, translation)]
    if invalid:
        samples = {index: translations[index][:160] for index in invalid}
        raise ValueError(
            f'The local translator returned a lossy or non-English translation at indexes {invalid}: {samples}'
        )
    if suspicious_collisions(dict(zip(values, translations))):
        raise ValueError('The local translator collapsed distinct inputs into one long translation.')
    return translations


def translate_resilient(client: httpx.Client, endpoint: str, model: str, values: list[str]) -> list[str]:
    try:
        return translate(client, endpoint, model, values)
    except Exception:
        if len(values) > 1:
            middle = len(values) // 2
            return (translate_resilient(client, endpoint, model, values[:middle]) +
                    translate_resilient(client, endpoint, model, values[middle:]))
        last_error = None
        for _ in range(4):
            try:
                return translate(client, endpoint, model, values, strict=True)
            except Exception as error:
                last_error = error
        raise last_error


def save(path: Path, model: str, source: Path, values: dict[str, str]):
    payload = {
        'format': 'CIRP local export translation cache',
        'model': model,
        'source_file': source.name,
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'translations': dict(sorted(values.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('source_json', type=Path)
    parser.add_argument('output_map', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--model', default='qwen3:8b')
    parser.add_argument('--live-base-url')
    parser.add_argument('--run-id')
    parser.add_argument('--extra-text', action='append', default=[])
    args = parser.parse_args()

    source = json.loads(args.source_json.read_text(encoding='utf-8'))
    live_required = set()
    if bool(args.live_base_url) != bool(args.run_id):
        parser.error('--live-base-url and --run-id must be supplied together.')
    if args.live_base_url and args.run_id:
        headers = {'X-CIRP-Client': 'browser'}
        with httpx.Client(timeout=120, headers=headers) as live_client:
            live_records = []
            for kind in ('MATERIAL', 'INSPECTION', 'CONFLICT', 'MISSING'):
                offset = 0
                while True:
                    records_response = live_client.get(
                        args.live_base_url.rstrip('/') +
                        f'/api/analysis-runs/{args.run_id}/record-summaries',
                        params={'kind': kind, 'offset': offset, 'limit': 500},
                    )
                    records_response.raise_for_status()
                    page = records_response.json()
                    live_records.extend(item['record'] for item in page['items'])
                    offset = page['pagination']['next_offset']
                    if offset is None:
                        break
            takeoffs_response = live_client.get(
                args.live_base_url.rstrip('/') + f'/api/analysis-runs/{args.run_id}/takeoffs'
            )
            takeoffs_response.raise_for_status()
        source['records'] = live_records
        live_required = set(strings_to_translate(live_records))
        source['analysis_support'] = takeoffs_response.json()
        source['verifications'] = {}
        source['evidence'] = []
    if 'records' in source and 'materials_and_equipment' not in source:
        from app.exporter import _readable_untranslated
        source = _readable_untranslated(source)
    required = sorted(
        set(strings_to_translate(source)) | live_required |
        {value for value in args.extra_text if CJK.search(value)}
    )
    translations = {}
    if args.output_map.exists():
        saved = json.loads(args.output_map.read_text(encoding='utf-8'))
        translations.update(saved.get('translations', {}))
    translations, removed = clean_translations(translations)
    if removed:
        save(args.output_map, args.model, args.source_json, translations)
        print(f'[WARN] removed_suspicious_or_lossy={removed}', flush=True)
    pending = sorted(
        (value for value in required if value not in translations or CJK.search(translations[value])),
        key=lambda value: (len(value), value),
    )
    work = list(batches(pending))
    print(f'[INFO] strings={len(required)} cached={len(required)-len(pending)} batches={len(work)}', flush=True)
    with httpx.Client(timeout=300) as client:
        for index, batch in enumerate(work, 1):
            last_error = None
            for attempt in range(2):
                try:
                    output = translate_resilient(client, args.endpoint, args.model, batch)
                    candidate = dict(translations)
                    candidate.update(zip(batch, output))
                    candidate, removed = clean_translations(candidate)
                    if any(value not in candidate for value in batch):
                        raise ValueError('The local translator produced a cross-batch collision or lossy translation.')
                    translations = candidate
                    save(args.output_map, args.model, args.source_json, translations)
                    print(f'[PASS] batch {index}/{len(work)} translated={len(batch)} total={len(translations)}', flush=True)
                    break
                except Exception as error:  # bounded retry for local, free processing only
                    last_error = error
                    time.sleep(1 + attempt)
            else:
                print(f'[FAIL] batch {index}/{len(work)}: {last_error}', flush=True)
                return 1
    print(f'[PASS] cache={args.output_map} translations={len(translations)}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
