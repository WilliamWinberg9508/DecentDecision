-- Wipe the questions, ballots and discussion, seed 25 fresh questions, and
-- create fifteen test agents -- one per model, every one under a billion
-- parameters. Pairs with vote_all.py, whose token list matches this one.
--
--   Get-Content reset_and_seed.sql | docker compose exec -T postgres psql -U vote -d vote -v ON_ERROR_STOP=1
--
-- Safe to re-run: it only ever removes the test accounts (@dd.test) and the
-- questions. Real accounts, yours included, are untouched -- the questions are
-- posted as the oldest real account, which is normally you.
--
-- WARNING: the test accounts have PREDICTABLE tokens (dd-test-01 ... dd-test-15)
-- so the voting script needs no configuration. Fine on localhost, unacceptable
-- on a public instance. Delete them before you publish:
--
--   DELETE FROM users WHERE email LIKE '%@dd.test';

BEGIN;

-- The questions are posted by the oldest account that is not a test one. With
-- no such account the insert below would select nothing and quietly create no
-- questions, which looks like success. Fail loudly instead, before anything
-- has been deleted.
DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@dd.test') THEN
        RAISE EXCEPTION 'No real account to author the questions. Register '
                        'yourself on the site first, then run this again.';
    END IF;
END $guard$;

-- TRUNCATE rather than DELETE: it does not fire the per-row tally triggers,
-- so this is instant. RESTART IDENTITY puts /i/1 back as the first question.
-- Every table referencing issues is named -- not CASCADE, which would silently
-- empty whatever comes to reference issues next. A new table stops this script
-- with an error instead of being quietly wiped by it.
TRUNCATE notifications, comment_votes, comments, votes, issues RESTART IDENTITY;

-- Old test accounts go, whatever tier created them, so there are never stale
-- agents left voting under a model they no longer run. Their agents cascade.
DELETE FROM users WHERE email LIKE '%@dd.test';

-- --- fifteen test accounts, one model each ------------------------------------
-- Eleven genuinely different sets of weights, plus four quantization variants
-- chosen as comparisons: qwen2.5:0.5b appears three times, at q2_K, the
-- default q4_K_M and fp16 -- the same model squashed three ways. Where they
-- disagree, quantization is the only thing that differs.

INSERT INTO users (email, email_canonical, display_name, username,
                   password_hash, status, email_verified, note)
SELECT m.slug || '@dd.test', m.slug || '@dd.test', m.slug, m.slug,
       NULL,                    -- no password: these cannot be logged into
       'approved', true, 'local test agent'
  FROM (VALUES
    ('qwen3-06b',       'qwen3:0.6b',                      'dd-test-01'),
    ('qwen3-06b-q8',    'qwen3:0.6b-q8_0',                 'dd-test-02'),
    ('qwen25-05b',      'qwen2.5:0.5b',                    'dd-test-03'),
    ('qwen25-05b-q2k',  'qwen2.5:0.5b-instruct-q2_K',      'dd-test-04'),
    ('qwen25-05b-fp16', 'qwen2.5:0.5b-instruct-fp16',      'dd-test-05'),
    ('qwen2-05b',       'qwen2:0.5b',                      'dd-test-06'),
    ('qwen15-05b',      'qwen:0.5b-chat',                  'dd-test-07'),
    ('gemma3-270m',     'gemma3:270m',                     'dd-test-08'),
    ('gemma3-270m-q8',  'gemma3:270m-it-q8_0',             'dd-test-09'),
    ('smollm2-360m',    'smollm2:360m',                    'dd-test-10'),
    ('smollm2-135m',    'smollm2:135m',                    'dd-test-11'),
    ('smollm-360m',     'smollm:360m-instruct-v0.2-q8_0',  'dd-test-12'),
    ('smollm-135m',     'smollm:135m-instruct-v0.2-q8_0',  'dd-test-13'),
    ('granite4-350m',   'granite4:350m',                   'dd-test-14'),
    ('granite4-350m-h', 'granite4:350m-h',                 'dd-test-15')
  ) AS m(slug, model, token);

INSERT INTO agents (user_id, name, model_name, token_hash)
SELECT u.id, u.username || '-agent', m.model,
       encode(sha256(m.token::bytea), 'hex')
  FROM (VALUES
    ('qwen3-06b',       'qwen3:0.6b',                      'dd-test-01'),
    ('qwen3-06b-q8',    'qwen3:0.6b-q8_0',                 'dd-test-02'),
    ('qwen25-05b',      'qwen2.5:0.5b',                    'dd-test-03'),
    ('qwen25-05b-q2k',  'qwen2.5:0.5b-instruct-q2_K',      'dd-test-04'),
    ('qwen25-05b-fp16', 'qwen2.5:0.5b-instruct-fp16',      'dd-test-05'),
    ('qwen2-05b',       'qwen2:0.5b',                      'dd-test-06'),
    ('qwen15-05b',      'qwen:0.5b-chat',                  'dd-test-07'),
    ('gemma3-270m',     'gemma3:270m',                     'dd-test-08'),
    ('gemma3-270m-q8',  'gemma3:270m-it-q8_0',             'dd-test-09'),
    ('smollm2-360m',    'smollm2:360m',                    'dd-test-10'),
    ('smollm2-135m',    'smollm2:135m',                    'dd-test-11'),
    ('smollm-360m',     'smollm:360m-instruct-v0.2-q8_0',  'dd-test-12'),
    ('smollm-135m',     'smollm:135m-instruct-v0.2-q8_0',  'dd-test-13'),
    ('granite4-350m',   'granite4:350m',                   'dd-test-14'),
    ('granite4-350m-h', 'granite4:350m-h',                 'dd-test-15')
  ) AS m(slug, model, token)
  JOIN users u ON u.username = m.slug;

-- --- 25 questions --------------------------------------------------------------

INSERT INTO issues (author_id, title, body, closes_at, created_at)
SELECT (SELECT id FROM users WHERE email NOT LIKE '%@dd.test'
         ORDER BY id LIMIT 1),
       t.title, t.body,
       now() + interval '14 days',
       now() - (t.ord * interval '17 minutes')
  FROM (VALUES
 (1,$t$Should supermarkets be required to donate unsold edible food?$t$,$b$Anything still fit to eat at closing goes to a registered charity rather than the bin. Shops carry the sorting cost and the liability question is unresolved.$b$),
 (2,$t$Should petrol lawn mowers be banned by 2030?$t$,$b$Six years' notice, electric and manual mowers unaffected. Falls hardest on people with large plots and no easy power supply.$b$),
 (3,$t$Should the public library lend out musical instruments?$t$,$b$A starter collection of about 60 instruments, borrowable for six weeks. Roughly 400 000 kr to set up, plus repairs.$b$),
 (4,$t$Should cyclists be allowed to treat red lights as give-way?$t$,$b$The Idaho stop: yield rather than stop when the road is clear. Fewer collisions in jurisdictions that have tried it; drivers find it infuriating.$b$),
 (5,$t$Should dogs be allowed on all public transport at all times?$t$,$b$Currently restricted to designated carriages. Allergies and anxious passengers on one side, dog owners without cars on the other.$b$),
 (6,$t$Should the harbour be closed to cruise ships?$t$,$b$About 40 calls a year. Ends the emissions and the crowding; ends roughly 90 million kr of local spending too.$b$),
 (7,$t$Should schools start no earlier than 09:00?$t$,$b$Adolescent sleep research is fairly clear. Working parents with 08:00 starts are the immediate problem.$b$),
 (8,$t$Should homework be abolished in primary school?$t$,$b$Years one to six. Evidence of academic benefit at that age is thin; some parents value the routine.$b$),
 (9,$t$Should phones be locked away during the school day?$t$,$b$Pouches at the door, returned at the final bell. Fewer distractions; no way for a parent to reach a child directly.$b$),
 (10,$t$Should street food stalls be allowed to trade without a permit?$t$,$b$Registration only, no fee and no waiting list. Lowers the barrier to starting; removes the tool for managing where stalls cluster.$b$),
 (11,$t$Should busking require an audition?$t$,$b$A panel grants pitches for a season. Raises the standard on the pedestrian street; also decides who is allowed to be heard.$b$),
 (12,$t$Should the museum be free on Sundays?$t$,$b$Costs about 1.2 million kr in lost admissions and would roughly double Sunday attendance.$b$),
 (13,$t$Should the municipality fund a public sauna?$t$,$b$By the harbour, 8 million kr to build and about 600 000 a year to run, with a small entry fee.$b$),
 (14,$t$Should graffiti walls be legalised in three locations?$t$,$b$Designated walls where painting is permitted. Evidence elsewhere suggests tagging elsewhere falls; some residents read it as surrender.$b$),
 (15,$t$Should the town clock be silenced between midnight and 06:00?$t$,$b$It has struck the hour since 1897. Forty-one households live within earshot.$b$),
 (16,$t$Should the annual fireworks be replaced with a drone show?$t$,$b$Higher cost for the first three years, then cheaper. Quieter for animals; less loved by people who like bangs.$b$),
 (17,$t$Should residents vote directly on 5% of the municipal budget?$t$,$b$Participatory budgeting on about 40 million kr, proposals from residents and a binding public vote.$b$),
 (18,$t$Should council members be limited to three terms?$t$,$b$Twelve years maximum. Ends entrenchment; also ends institutional memory and hands more power to permanent officials.$b$),
 (19,$t$Should meetings between council members and lobbyists be logged publicly?$t$,$b$Who met whom, when and about what, published monthly. Some conversations would move somewhere unlogged.$b$),
 (20,$t$Should applicant names be hidden when shortlisting municipal jobs?$t$,$b$Anonymous until interview. Evidence on the effect is mixed and depends heavily on what else changes.$b$),
 (21,$t$Should empty shops be taxed after twelve months of vacancy?$t$,$b$A vacancy levy on ground-floor retail. Pushes owners to let or sell; punishes those who cannot find a tenant.$b$),
 (22,$t$Should the number of fast-food outlets near schools be capped?$t$,$b$No new licences within 400 metres of a school gate. Existing outlets unaffected.$b$),
 (23,$t$Should the municipality buy back the water network?$t$,$b$Roughly 2 billion kr to return it to public ownership. Long-term control of a monopoly, against a generation of debt.$b$),
 (24,$t$Should parking permits be priced by vehicle weight?$t$,$b$A two-tonne car occupies the same space but does more damage to the road surface and to whoever it hits.$b$),
 (25,$t$Should there be a car-free Sunday once a month?$t$,$b$The centre closed to cars one Sunday in four. A street festival for some, a lost trading day for others.$b$)
  ) AS t(ord, title, body);

COMMIT;

-- What you should see: 25 questions, 0 ballots, 15 test agents, and 0 titles
-- that are not questions.
SELECT (SELECT count(*) FROM issues) AS questions,
       (SELECT count(*) FROM votes)  AS ballots,
       (SELECT count(*) FROM agents a JOIN users u ON u.id = a.user_id
         WHERE u.email LIKE '%@dd.test') AS test_agents,
       (SELECT count(*) FROM issues WHERE title NOT LIKE '%?') AS titles_not_questions;
