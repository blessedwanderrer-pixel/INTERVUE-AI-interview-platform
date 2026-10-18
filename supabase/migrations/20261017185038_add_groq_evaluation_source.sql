alter table public.interview_evaluations
  drop constraint interview_evaluations_evaluation_type_check;

alter table public.interview_evaluations
  add constraint interview_evaluations_evaluation_type_check
  check (evaluation_type in ('heuristic', 'gemini', 'groq'));

