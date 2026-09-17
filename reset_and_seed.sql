-- Wipe the questions and ballots, seed 50 fresh questions, and create ten
-- test agents — one per model.
--
--   docker compose exec -T postgres psql -U vote -d vote -f - < reset_and_seed.sql
--
-- WARNING: the ten test accounts below have PREDICTABLE tokens (dd-test-01 …
-- dd-test-10) so the voting script can use them without you copying anything.
-- That is fine on localhost and unacceptable on a public instance. Delete them
-- before you publish:
--
--   DELETE FROM users WHERE email LIKE '%@dd.test';
--
-- Your own account, its agent and its real token are untouched.

BEGIN;

-- TRUNCATE rather than DELETE: it does not fire the per-row tally trigger, so
-- this is instant instead of one UPDATE per ballot. RESTART IDENTITY puts the
-- ids back to 1 so /i/1 is the first question again.
--
-- Every table that references issues has to be named here. Not CASCADE: this
-- file is run by hand against a database with real rows in it, and CASCADE
-- would silently empty whatever else comes to reference issues later. Naming
-- them means a future table stops this script with an error rather than
-- quietly being wiped by it.
TRUNCATE notifications, comment_votes, comments, votes, issues RESTART IDENTITY;

-- --- ten test accounts, one model each ---------------------------------------
-- The model names below are a starting value only: the agents table is
-- corrected from each ballot as it arrives, so running vote_all.py on the tiny
-- tier re-labels accounts 01-06 on their first vote. No reseeding to switch.

INSERT INTO users (email, display_name, username, password_hash, status, note)
SELECT lower(m.slug) || '@dd.test', m.slug, m.slug,
       NULL,                       -- no password: these cannot be logged into
       'approved', 'local test agent'
  FROM (VALUES
    ('qwen3-06b'), ('qwen3-17b'), ('qwen3-4b'), ('llama32-1b'), ('llama32-3b'),
    ('gemma3-1b'), ('gemma3-4b'), ('phi4mini'), ('qwen25-15b'), ('qwen25-3b')
  ) AS m(slug)
ON CONFLICT (email) DO UPDATE SET status = 'approved';

INSERT INTO agents (user_id, name, model_name, token_hash)
SELECT u.id, u.username || '-agent', m.model,
       encode(sha256(m.token::bytea), 'hex')
  FROM (VALUES
    ('qwen3-06b',  'qwen3:0.6b',    'dd-test-01'),
    ('qwen3-17b',  'qwen3:1.7b',    'dd-test-02'),
    ('qwen3-4b',   'qwen3:4b',      'dd-test-03'),
    ('llama32-1b', 'llama3.2:1b',   'dd-test-04'),
    ('llama32-3b', 'llama3.2:3b',   'dd-test-05'),
    ('gemma3-1b',  'gemma3:1b',     'dd-test-06'),
    ('gemma3-4b',  'gemma3:4b',     'dd-test-07'),
    ('phi4mini',   'phi4-mini:3.8b','dd-test-08'),
    ('qwen25-15b', 'qwen2.5:1.5b',  'dd-test-09'),
    ('qwen25-3b',  'qwen2.5:3b',    'dd-test-10')
  ) AS m(slug, model, token)
  JOIN users u ON u.username = m.slug
ON CONFLICT (user_id) DO UPDATE
  SET model_name = EXCLUDED.model_name,
      token_hash = EXCLUDED.token_hash;

-- --- 50 questions ------------------------------------------------------------

-- The questions are posted by the oldest account that is not one of the test
-- ones -- normally you. On a database where no such account exists yet, the
-- insert below would select nothing and quietly create no questions, which
-- looks like the script having worked. Fail loudly instead.
DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@dd.test') THEN
        RAISE EXCEPTION 'No real account to author the questions. Register '
                        'yourself on the site first, then run this again.';
    END IF;
END $guard$;

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
 (4,$t$Should construction noise be banned before 08:00?$t$,$b$Currently permitted from 07:00 on weekdays. Pushes build schedules later and adds cost to projects on tight timelines.$b$),
 (5,$t$Should the municipality buy the empty factory and turn it into workshops?$t$,$b$18 million kr for the building, then let at cost to makers and small manufacturers. It has stood empty for six years.$b$),
 (6,$t$Should cyclists be allowed to treat red lights as give-way?$t$,$b$The Idaho stop: yield rather than stop when the road is clear. Fewer collisions in jurisdictions that have tried it; drivers find it infuriating.$b$),
 (7,$t$Should every new road include a separated bike lane?$t$,$b$No exceptions for narrow streets, which in practice means some streets lose a lane of parking.$b$),
 (8,$t$Should residents be paid to replace lawns with meadow?$t$,$b$200 kr per square metre, capped at 100 square metres per household. Water saving and pollinators, against a visible cost per participant.$b$),
 (9,$t$Should dogs be allowed on all public transport at all times?$t$,$b$Currently restricted to designated carriages. Allergies and anxious passengers on one side, dog owners without cars on the other.$b$),
 (10,$t$Should the harbour be closed to cruise ships?$t$,$b$About 40 calls a year. Ends the emissions and the crowding; ends roughly 90 million kr of local spending too.$b$),
 (11,$t$Should schools start no earlier than 09:00?$t$,$b$Adolescent sleep research is fairly clear. Working parents with 08:00 starts are the immediate problem.$b$),
 (12,$t$Should homework be abolished in primary school?$t$,$b$Years one to six. Evidence of academic benefit at that age is thin; some parents value the routine.$b$),
 (13,$t$Should every school have a full-time nurse?$t$,$b$Currently one nurse covers three schools. Around 14 million kr a year for the additional posts.$b$),
 (14,$t$Should phones be locked away during the school day?$t$,$b$Pouches at the door, returned at the final bell. Fewer distractions; no way for a parent to reach a child directly.$b$),
 (15,$t$Should the school year be shortened by two weeks?$t$,$b$Teaching days redistributed, not lost. Cheaper heating and happier staff; two more weeks of childcare to arrange.$b$),
 (16,$t$Should the municipality run its own pharmacy?$t$,$b$One municipal pharmacy in the district where the last private one closed. Runs at a loss of about 2 million kr a year.$b$),
 (17,$t$Should GP appointments be bookable without a phone call?$t$,$b$Online and in person as alternatives to the 08:00 telephone queue. Requires a booking system and a policy for people without internet.$b$),
 (18,$t$Should free period products be available in all public buildings?$t$,$b$Schools, libraries, sports halls and the town hall. About 900 000 kr a year.$b$),
 (19,$t$Should the city fund a 24-hour crisis line?$t$,$b$Staffed locally rather than routed to a national number. 4 million kr a year, and it would be used most at 03:00.$b$),
 (20,$t$Should free eye tests be provided for all schoolchildren?$t$,$b$Every child screened twice during compulsory school. Undiagnosed vision problems are routinely mistaken for learning difficulties.$b$),
 (21,$t$Should street food stalls be allowed to trade without a permit?$t$,$b$Registration only, no fee and no waiting list. Lowers the barrier to starting; removes the tool for managing where stalls cluster.$b$),
 (22,$t$Should the Christmas market be restricted to local traders?$t$,$b$Stalls reserved for businesses within 50 km. Keeps the money local; a smaller market with less variety.$b$),
 (23,$t$Should busking require an audition?$t$,$b$A panel grants pitches for a season. Raises the standard on the pedestrian street; also decides who is allowed to be heard.$b$),
 (24,$t$Should the museum be free on Sundays?$t$,$b$Costs about 1.2 million kr in lost admissions and would roughly double Sunday attendance.$b$),
 (25,$t$Should the municipality fund a public sauna?$t$,$b$By the harbour, 8 million kr to build and about 600 000 a year to run, with a small entry fee.$b$),
 (26,$t$Should there be a public piano at the station?$t$,$b$One upright, tuned monthly. Delightful roughly 80% of the time.$b$),
 (27,$t$Should graffiti walls be legalised in three locations?$t$,$b$Designated walls where painting is permitted. Evidence elsewhere suggests tagging elsewhere falls; some residents read it as surrender.$b$),
 (28,$t$Should the city commission a statue of a local inventor?$t$,$b$1.4 million kr for a bronze in the square. Statues are permanent in a way that opinions about people are not.$b$),
 (29,$t$Should the town clock be silenced between midnight and 06:00?$t$,$b$It has struck the hour since 1897. Forty-one households live within earshot.$b$),
 (30,$t$Should the annual fireworks be replaced with a drone show?$t$,$b$Higher cost for the first three years, then cheaper. Quieter for animals; less loved by people who like bangs.$b$),
 (31,$t$Should residents vote directly on 5% of the municipal budget?$t$,$b$Participatory budgeting on about 40 million kr, proposals from residents and a binding public vote.$b$),
 (32,$t$Should council members be limited to three terms?$t$,$b$Twelve years maximum. Ends entrenchment; also ends institutional memory and hands more power to permanent officials.$b$),
 (33,$t$Should the council publish its own carbon footprint every year?$t$,$b$Audited, covering buildings, fleet and procurement. About 700 000 kr a year to produce properly.$b$),
 (34,$t$Should meetings between council members and lobbyists be logged publicly?$t$,$b$Who met whom, when and about what, published monthly. Some conversations would move somewhere unlogged.$b$),
 (35,$t$Should the municipality divest its fossil fuel holdings?$t$,$b$About 340 million kr in the pension fund, to be sold within three years regardless of return.$b$),
 (36,$t$Should the city refuse contracts with firms that use unpaid interns?$t$,$b$A procurement condition with self-declaration and spot checks. Narrows the supplier field.$b$),
 (37,$t$Should applicant names be hidden when shortlisting municipal jobs?$t$,$b$Anonymous until interview. Evidence on the effect is mixed and depends heavily on what else changes.$b$),
 (38,$t$Should the council meet in the evening so working people can attend?$t$,$b$Meetings move from 13:00 to 18:00. Better access for residents; worse for members with young children.$b$),
 (39,$t$Should every decision over 10 million kr require a published cost-benefit analysis?$t$,$b$Before the vote, not after. Slows decisions by roughly six weeks each.$b$),
 (40,$t$Should the municipality publish a register of every property it owns?$t$,$b$Address, use, condition and cost. Nobody currently has the complete list in one place, including the municipality.$b$),
 (41,$t$Should empty shops be taxed after twelve months of vacancy?$t$,$b$A vacancy levy on ground-floor retail. Pushes owners to let or sell; punishes those who cannot find a tenant.$b$),
 (42,$t$Should the number of fast-food outlets near schools be capped?$t$,$b$No new licences within 400 metres of a school gate. Existing outlets unaffected.$b$),
 (43,$t$Should landlords be required to publish energy ratings in every advert?$t$,$b$A rating on every listing, as with appliances. Cheap to do and unwelcome to owners of cold buildings.$b$),
 (44,$t$Should new offices be refused in the centre in favour of housing?$t$,$b$Central plots go to homes. There is a nine-year housing queue and a 14% office vacancy rate.$b$),
 (45,$t$Should the municipality buy back the water network?$t$,$b$Roughly 2 billion kr to return it to public ownership. Long-term control of a monopoly, against a generation of debt.$b$),
 (46,$t$Should parking permits be priced by vehicle weight?$t$,$b$A two-tonne car occupies the same space but does more damage to the road surface and to whoever it hits.$b$),
 (47,$t$Should there be a free bike repair station in every district?$t$,$b$Pump, tools and a stand, unattended. About 25 000 kr each plus vandalism.$b$),
 (48,$t$Should electric scooters be limited to 15 km/h in the centre?$t$,$b$Geofenced speed cap rather than a ban. Slower than most cyclists, which creates its own problem.$b$),
 (49,$t$Should buses run all night at weekends?$t$,$b$Friday and Saturday, hourly. About 9 million kr a year for a service that will often carry very few people.$b$),
 (50,$t$Should there be a car-free Sunday once a month?$t$,$b$The centre closed to cars one Sunday in four. A street festival for some, a lost trading day for others.$b$)
  ) AS t(ord, title, body)
 WHERE EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@dd.test');

COMMIT;

-- What you should see: 50 questions, 0 votes, and ten approved test agents.
SELECT (SELECT count(*) FROM issues) AS questions,
       (SELECT count(*) FROM votes)  AS votes,
       (SELECT count(*) FROM users WHERE email LIKE '%@dd.test') AS test_agents,
       (SELECT count(*) FROM issues WHERE title NOT LIKE '%?') AS titles_not_questions;
