-- Voice-to-voice latency + per-stage TTFB from past real calls (workflow_runs.logs).
-- LATENCY_MEASURED = VAD "user stopped" -> bot started speaking; add VAD stop_secs (0.2s) for perceived latency.
with ev as (
  select r.id, r.mode, r.created_at, e
  from workflow_runs r, json_array_elements(r.logs::json -> 'realtime_feedback_events') e
  where r.logs::jsonb ? 'realtime_feedback_events'
)
select
  id, mode, created_at::date,
  count(*) filter (where e->>'type' = 'rtf-latency-measured') as turns,
  round(percentile_cont(0.5) within group (order by (e->'payload'->>'latency_seconds')::float)
        filter (where e->>'type' = 'rtf-latency-measured')::numeric, 2) as v2v_p50,
  round(max((e->'payload'->>'latency_seconds')::float)
        filter (where e->>'type' = 'rtf-latency-measured')::numeric, 2) as v2v_max,
  round(avg((e->'payload'->>'ttfb_seconds')::float) filter (where e->>'type' = 'rtf-ttfb-metric' and e->'payload'->>'kind' = 'stt')::numeric, 2) as stt,
  round(avg((e->'payload'->>'ttfb_seconds')::float) filter (where e->>'type' = 'rtf-ttfb-metric' and coalesce(e->'payload'->>'kind','llm') = 'llm')::numeric, 2) as llm,
  round(avg((e->'payload'->>'ttfb_seconds')::float) filter (where e->>'type' = 'rtf-ttfb-metric' and e->'payload'->>'kind' = 'tts')::numeric, 2) as tts
from ev
group by id, mode, created_at
having count(*) filter (where e->>'type' = 'rtf-latency-measured') > 0
order by created_at desc
limit 25;
