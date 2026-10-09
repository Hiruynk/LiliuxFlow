"""Bounded PC3-native metadata only; unvalidated text is never mirrored.

SPDX-License-Identifier: Apache-2.0
"""
import re

ID = r'(req-[0-9]{1,20}-[0-9]{1,20})'
NUMBER = r'([0-9]{1,12})'
ROUTE = re.compile(r'^LILY_QSA_ROUTE_EFFECTIVE requested=(unset|query|split|invalid) route=(query|split)$')
DISPATCH = re.compile(r'^LILY_QSA_DISPATCH_METADATA sparse_prefill_rows=' + NUMBER + r' route=(query|split) split_dispatch_count=([01]) query_dispatch_count=([01])$')
ENGINE = re.compile(r'^LILY_ENGINE_EFFECTIVE context_tokens=' + NUMBER + r' kv_cache=(bf16|q8) mtp_drafts=([0-9]{1,3}) max_batch=([0-9]{1,3})$')
PRELOAD = re.compile(r'^paged weights: ([0-9]{1,3})\.([0-9]) GB resident after the background preload (finished|stopped) in ([0-9]{1,3})\.([0-9])s \(([0-9]{1,3})\.([0-9]) GB read\)$')
ACQUIRE = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=lane_acquired queue_wait_ms=' + NUMBER + '$')
RELEASE = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=lane_released cancelled=(true|false) lane_ms=' + NUMBER + '$')
PROGRESS = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=prefill_progress completed_chunks=' + NUMBER + r' prefilled_tokens=' + NUMBER + '$')
ENTER = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=queue_enter$')
TERMINAL = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=(queue_rejected status=(500|503)|queue_cancelled queue_wait_ms=[0-9]{1,12}) queue_slot_released=true$')
AFTER_LOAD = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=cancelled_after_engine_ready queue_wait_ms=' + NUMBER + r' model_load_aborted=false$')
DECODE_DROP = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=decode_session_cancelled session_disposition=dropped$')
PREFILL_DROP = re.compile(r'^\[lily-meta\] request_id=' + ID + r' phase=prefill_cancelled stage=(after_session_acquire|before_vision_image|after_vision_image|after_durable_snapshot|prefill_chunk|before_session_checkpoint|after_session_checkpoint) completed_chunks=' + NUMBER + r' prefilled_tokens=' + NUMBER + r' session_disposition=dropped$')
CRITICAL = ('LILY_QSA_ROUTE_EFFECTIVE', 'LILY_QSA_DISPATCH_METADATA', 'LILY_ENGINE_EFFECTIVE')


def consume_latest(state, raw):
    """True means full fixed grammar parsed, so the caller may mirror it."""
    parsers = (ROUTE, DISPATCH, ENGINE, PRELOAD, ACQUIRE, RELEASE, PROGRESS,
               ENTER, TERMINAL, AFTER_LOAD, DECODE_DROP, PREFILL_DROP)
    found = next(((pattern, match) for pattern in parsers if (match := pattern.fullmatch(raw))), None)
    if found is None:
        if raw.startswith(CRITICAL) or raw.startswith('[lily-meta]'):
            with state.lock:
                state.record['proof_valid'] = False
                state._write()
        return False  # Never persist or mirror malformed/unknown/raw input.
    pattern, match = found
    values = match.groups()
    with state.lock:
        record = state.record
        def fail():
            record['proof_valid'] = False
        def fixed(field, value):
            prior = record.get(field)
            if prior is None:
                record[field] = value
            elif prior != value:
                fail()
        if pattern is PRELOAD:
            resident = int(values[0])*10 + int(values[1])
            seconds = int(values[3])*10 + int(values[4])
            read = int(values[5])*10 + int(values[6])
            if not 1 <= resident <= 1280 or not 0 <= seconds <= 9000 or not 0 <= read <= 1280:
                fail()
            else:
                fixed('ngram_preload_observed', {'status':values[2], 'resident_gb_tenths':resident,
                      'elapsed_s_tenths':seconds, 'read_gb_tenths':read})
        elif pattern is ROUTE:
            requested, selected = values
            fixed('qsa_route_effective', {'requested': requested, 'route': selected})
            if (requested, selected) != ('split', 'split'):
                fail()
        elif pattern is DISPATCH:
            rows, route, split, query = values
            value = {'sparse_prefill_rows': int(rows), 'route': route,
                     'split_dispatch_count': int(split), 'query_dispatch_count': int(query)}
            fixed('qsa_dispatch_metadata', value)
            if not 16 <= int(rows) <= 65536 or (route, split, query) != ('split', '1', '0'):
                fail()
        elif pattern is ENGINE:
            context, kv, mtp, batch = values
            value = {'context_tokens': int(context), 'kv_cache': kv,
                     'mtp_drafts': int(mtp), 'max_batch': int(batch)}
            fixed('native_engine_effective', value)
            if value != {'context_tokens': 65536, 'kv_cache': 'bf16', 'mtp_drafts': 2, 'max_batch': 1}:
                fail()
            record['native_context_tokens'] = value['context_tokens']
        elif pattern is ENTER:
            request_id = values[0]
            pending = record['queued_request_ids']
            if request_id in pending or request_id in record['active_lane_ids'] or len(pending) >= 4:
                fail()
            else:
                pending.append(request_id)
            record['queue_event_sequence'] += 1
            record['queue_entered_sequence'] += 1
        elif pattern in (TERMINAL, AFTER_LOAD):
            request_id = values[0]
            if request_id not in record['queued_request_ids'] or request_id in record['active_lane_ids']:
                fail()
            else:
                record['queued_request_ids'].remove(request_id)
            record['queue_event_sequence'] += 1
            record['queue_terminal_sequence'] += 1
        elif pattern is ACQUIRE:
            request_id = values[0]
            if record['active_lane_ids'] or request_id not in record['queued_request_ids']:
                fail()
            else:
                record['queued_request_ids'].remove(request_id)
                record['active_lane_ids'].append(request_id)
            record['event_sequence'] += 1
            record['acquired_sequence'] += 1
            record['queue_acquired_sequence'] += 1
            record['queue_event_sequence'] += 1
            record['last_acquired_request_id'] = request_id
            record['last_prefill_progress'] = None
            record['cancelled_session_dropped'] = False
        elif pattern is RELEASE:
            request_id, cancelled, _ = values
            if record['active_lane_ids'] != [request_id]:
                fail()
            else:
                record['active_lane_ids'].clear()
            record['event_sequence'] += 1
            record['released_sequence'] += 1
            record['last_released_request_id'] = request_id
            record['last_release_cancelled'] = cancelled == 'true'
        elif pattern is PROGRESS:
            request_id, chunks, tokens = values
            if record['active_lane_ids'] != [request_id] or not 1 <= int(chunks) <= 65536 or not 1 <= int(tokens) <= 65536:
                fail()
            elif record.get('last_prefill_progress') is not None:
                fail()  # Native emits one first-positive record for the request.
            else:
                record['last_prefill_progress'] = {'request_id': request_id,
                    'completed_chunks': int(chunks), 'prefilled_tokens': int(tokens)}
        elif pattern in (DECODE_DROP, PREFILL_DROP):
            request_id = values[0]
            progress = record.get('last_prefill_progress')
            if record['active_lane_ids'] != [request_id]:
                fail()
            elif pattern is PREFILL_DROP and (int(values[2]) > 65536 or int(values[3]) > 65536):
                fail()
            elif (pattern is PREFILL_DROP and progress is not None
                  and (int(values[2]) < progress['completed_chunks']
                       or int(values[3]) < progress['prefilled_tokens'])):
                fail()
            else:
                record['last_cancelled_request_id'] = request_id
                record['cancelled_session_dropped'] = True
        state._write()
    return record['proof_valid'] is True  # Never mirror semantically invalid records.


def preload_finished(record):
    """Observed native completion, not argv=true, time waited, or GPU timing."""
    value = record.get('ngram_preload_observed')
    return (record.get('proof_valid') is True and isinstance(value,dict)
            and set(value)=={'status','resident_gb_tenths','elapsed_s_tenths','read_gb_tenths'}
            and value['status']=='finished'
            and all(type(value[k]) is int for k in ('resident_gb_tenths','elapsed_s_tenths','read_gb_tenths'))
            and 1 <= value['resident_gb_tenths'] <= 1280
            and 0 <= value['elapsed_s_tenths'] <= 9000 and 0 <= value['read_gb_tenths'] <= 1280)
