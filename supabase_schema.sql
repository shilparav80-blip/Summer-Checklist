create table if not exists public.star_quest_profiles (
  player_slug text primary key,
  player_name text not null,
  state jsonb not null default '{"tasks": null, "penalty_rules": null, "done": {}, "pending": {}, "penalties": {}, "schedule": {}, "locked": {}}'::jsonb,
  total_stars integer not null default 0,
  updated_at timestamptz not null default now()
);

create or replace function public.set_star_quest_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists star_quest_profiles_updated_at on public.star_quest_profiles;

create trigger star_quest_profiles_updated_at
before update on public.star_quest_profiles
for each row
execute function public.set_star_quest_updated_at();
