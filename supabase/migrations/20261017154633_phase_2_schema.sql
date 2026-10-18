create extension if not exists pgcrypto;

create table public.candidate_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 120),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.cv_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  original_name text not null check (char_length(original_name) between 1 and 255),
  storage_path text not null unique,
  mime_type text not null check (mime_type in ('application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain')),
  size_bytes integer not null check (size_bytes > 0 and size_bytes <= 5242880),
  extracted_text text,
  extraction_status text not null check (extraction_status in ('complete', 'failed')),
  extraction_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.interview_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  cv_id uuid references public.cv_documents(id) on delete set null,
  role text not null check (char_length(role) between 1 and 160),
  seniority text not null check (seniority in ('Early career', 'Mid-level', 'Senior', 'Lead')),
  focus text not null check (focus in ('behavioral', 'technical', 'leadership')),
  duration_questions smallint not null check (duration_questions in (4, 6, 8)),
  current_question_index smallint not null default 0 check (current_question_index >= 0),
  status text not null default 'in_progress' check (status in ('in_progress', 'completed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create table public.interview_questions (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.interview_sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  position smallint not null check (position >= 0),
  question_text text not null check (char_length(question_text) between 1 and 2000),
  created_at timestamptz not null default now(),
  unique (session_id, position)
);

create table public.candidate_answers (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.interview_sessions(id) on delete cascade,
  question_id uuid not null references public.interview_questions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  answer_text text not null check (char_length(answer_text) between 1 and 12000),
  word_count integer not null check (word_count > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (question_id)
);

create table public.interview_evaluations (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null unique references public.interview_sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  evaluation_type text not null default 'heuristic' check (evaluation_type = 'heuristic'),
  rubric_version text not null,
  notice text not null,
  overall_score smallint not null check (overall_score between 0 and 100),
  scores jsonb not null check (jsonb_typeof(scores) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index cv_documents_user_id_idx on public.cv_documents(user_id);
create index interview_sessions_user_id_idx on public.interview_sessions(user_id, created_at desc);
create index interview_questions_session_id_idx on public.interview_questions(session_id, position);
create index candidate_answers_session_id_idx on public.candidate_answers(session_id, created_at);
create index interview_evaluations_user_id_idx on public.interview_evaluations(user_id, created_at desc);

alter table public.candidate_profiles enable row level security;
alter table public.cv_documents enable row level security;
alter table public.interview_sessions enable row level security;
alter table public.interview_questions enable row level security;
alter table public.candidate_answers enable row level security;
alter table public.interview_evaluations enable row level security;

create policy "profiles_select_own" on public.candidate_profiles for select to authenticated using ((select auth.uid()) = user_id);
create policy "profiles_insert_own" on public.candidate_profiles for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "profiles_update_own" on public.candidate_profiles for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "profiles_delete_own" on public.candidate_profiles for delete to authenticated using ((select auth.uid()) = user_id);

create policy "cvs_select_own" on public.cv_documents for select to authenticated using ((select auth.uid()) = user_id);
create policy "cvs_insert_own" on public.cv_documents for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "cvs_update_own" on public.cv_documents for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "cvs_delete_own" on public.cv_documents for delete to authenticated using ((select auth.uid()) = user_id);

create policy "sessions_select_own" on public.interview_sessions for select to authenticated using ((select auth.uid()) = user_id);
create policy "sessions_insert_own" on public.interview_sessions for insert to authenticated with check ((select auth.uid()) = user_id and (cv_id is null or exists (select 1 from public.cv_documents c where c.id = cv_id and c.user_id = (select auth.uid()))));
create policy "sessions_update_own" on public.interview_sessions for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id and (cv_id is null or exists (select 1 from public.cv_documents c where c.id = cv_id and c.user_id = (select auth.uid()))));
create policy "sessions_delete_own" on public.interview_sessions for delete to authenticated using ((select auth.uid()) = user_id);

create policy "questions_select_own" on public.interview_questions for select to authenticated using ((select auth.uid()) = user_id);
create policy "questions_insert_own" on public.interview_questions for insert to authenticated with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_sessions s where s.id = session_id and s.user_id = (select auth.uid())));
create policy "questions_update_own" on public.interview_questions for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_sessions s where s.id = session_id and s.user_id = (select auth.uid())));
create policy "questions_delete_own" on public.interview_questions for delete to authenticated using ((select auth.uid()) = user_id);

create policy "answers_select_own" on public.candidate_answers for select to authenticated using ((select auth.uid()) = user_id);
create policy "answers_insert_own" on public.candidate_answers for insert to authenticated with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_questions q where q.id = question_id and q.session_id = session_id and q.user_id = (select auth.uid())));
create policy "answers_update_own" on public.candidate_answers for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_questions q where q.id = question_id and q.session_id = session_id and q.user_id = (select auth.uid())));
create policy "answers_delete_own" on public.candidate_answers for delete to authenticated using ((select auth.uid()) = user_id);

create policy "evaluations_select_own" on public.interview_evaluations for select to authenticated using ((select auth.uid()) = user_id);
create policy "evaluations_insert_own" on public.interview_evaluations for insert to authenticated with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_sessions s where s.id = session_id and s.user_id = (select auth.uid())));
create policy "evaluations_update_own" on public.interview_evaluations for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id and exists (select 1 from public.interview_sessions s where s.id = session_id and s.user_id = (select auth.uid())));
create policy "evaluations_delete_own" on public.interview_evaluations for delete to authenticated using ((select auth.uid()) = user_id);

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('candidate-cvs', 'candidate-cvs', false, 5242880, array['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain'])
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

create policy "cv_storage_select_own" on storage.objects for select to authenticated
using (bucket_id = 'candidate-cvs' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "cv_storage_insert_own" on storage.objects for insert to authenticated
with check (bucket_id = 'candidate-cvs' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "cv_storage_update_own" on storage.objects for update to authenticated
using (bucket_id = 'candidate-cvs' and (storage.foldername(name))[1] = (select auth.uid())::text)
with check (bucket_id = 'candidate-cvs' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "cv_storage_delete_own" on storage.objects for delete to authenticated
using (bucket_id = 'candidate-cvs' and (storage.foldername(name))[1] = (select auth.uid())::text);

grant usage on schema public to authenticated;
grant select, insert, update, delete on public.candidate_profiles, public.cv_documents, public.interview_sessions, public.interview_questions, public.candidate_answers, public.interview_evaluations to authenticated;

