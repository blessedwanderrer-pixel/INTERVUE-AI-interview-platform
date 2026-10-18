alter table public.interview_sessions
  add column mode text not null default 'text'
  constraint interview_sessions_mode_check check (mode in ('text', 'voice'));

alter table public.interview_sessions drop constraint interview_sessions_status_check;
alter table public.interview_sessions
  add constraint interview_sessions_status_check check (status in ('in_progress', 'interrupted', 'completed'));

alter table public.interview_evaluations drop constraint interview_evaluations_session_id_key;
alter table public.interview_evaluations drop constraint interview_evaluations_evaluation_type_check;
alter table public.interview_evaluations
  add constraint interview_evaluations_evaluation_type_check check (evaluation_type in ('heuristic', 'gemini')),
  add column details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
  add column model_name text;
alter table public.interview_evaluations
  add constraint interview_evaluations_session_type_key unique (session_id, evaluation_type);

create table public.interview_transcript_turns (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.interview_sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  client_event_id text not null check (char_length(client_event_id) between 8 and 120),
  speaker text not null check (speaker in ('interviewer', 'candidate')),
  content text not null check (char_length(content) between 1 and 12000),
  position integer not null check (position >= 0),
  occurred_at timestamptz,
  created_at timestamptz not null default now(),
  unique (session_id, client_event_id),
  unique (session_id, position)
);

create index interview_transcript_turns_user_id_idx on public.interview_transcript_turns(user_id, created_at);
create index candidate_answers_user_id_idx on public.candidate_answers(user_id);
create index interview_questions_user_id_idx on public.interview_questions(user_id);
create index interview_sessions_cv_id_idx on public.interview_sessions(cv_id) where cv_id is not null;

alter table public.interview_transcript_turns enable row level security;

create policy "transcript_select_own" on public.interview_transcript_turns for select to authenticated
using ((select auth.uid()) = user_id);
create policy "transcript_insert_own" on public.interview_transcript_turns for insert to authenticated
with check ((select auth.uid()) = user_id and exists (
  select 1 from public.interview_sessions s
  where s.id = session_id and s.user_id = (select auth.uid())
));
create policy "transcript_update_own" on public.interview_transcript_turns for update to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id and exists (
  select 1 from public.interview_sessions s
  where s.id = session_id and s.user_id = (select auth.uid())
));
create policy "transcript_delete_own" on public.interview_transcript_turns for delete to authenticated
using ((select auth.uid()) = user_id);

grant select, insert, update, delete on public.interview_transcript_turns to authenticated;

