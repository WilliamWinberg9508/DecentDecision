-- Launch reset: the site as it should look on the day it goes public.
--
-- Removes EVERY issue, ballot, comment and notification, removes the test
-- agent accounts (anything @dd.test, whose tokens are predictable), and posts
-- one friendly, everyday issue in each of the 25 country forums. Real accounts,
-- yours included, are kept; the issues are posted as the oldest real account.
--
-- This deletes data and there is no undo: take a backup first if anything in
-- the database matters (see DEPLOY.md). Then, from the project folder:
--
--   docker compose cp launch_seed.sql postgres:/tmp/launch_seed.sql
--   docker compose exec postgres psql -U vote -d vote -v ON_ERROR_STOP=1 -f /tmp/launch_seed.sql

SET client_encoding TO 'UTF8';
BEGIN;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE email NOT LIKE '%@dd.test') THEN
        RAISE EXCEPTION 'No real account to post the issues. Register yourself '
                        'on the site first, then run this again.';
    END IF;
    IF (SELECT count(*) FROM forums WHERE kind = 'country') < 25 THEN
        RAISE EXCEPTION 'The country forums are missing. Start the updated app '
                        '(docker compose up -d --build) once, then run this again.';
    END IF;
END $guard$;

TRUNCATE notifications, comment_votes, comments, votes, issues RESTART IDENTITY;
DELETE FROM users WHERE email LIKE '%@dd.test';        -- their agents go with them

INSERT INTO issues (author_id, forum_id, title, body, closes_at, created_at)
SELECT (SELECT id FROM users WHERE email NOT LIKE '%@dd.test' ORDER BY id LIMIT 1),
       f.id, t.title, t.body,
       now() + interval '30 days',
       now() - (t.ord * interval '3 minutes')
  FROM (VALUES
 (1,'china',$t$Should Chinese cities add more free drinking-water fountains in parks and metro stations?$t$,$b$Refilling a bottle is often hard away from home. Fountains in parks and stations would cut plastic waste and help in hot summers; each one costs money to install, keep clean and test.$b$),
 (2,'india',$t$Should Indian cities plant shade trees along every new bus route?$t$,$b$Waiting for a bus in the midday sun is hard, especially for older people. Trees cool pavements and clean the air, but need water, space and care for years before they give much shade.$b$),
 (3,'united-states',$t$Should US national parks offer free entry on more days each year?$t$,$b$Parks already have a handful of fee-free days. More of them would open the parks to families on tight budgets, but entry fees help pay for trails, rangers and repairs.$b$),
 (4,'indonesia',$t$Should Jakarta build more covered walkways linking train stations to bus stops?$t$,$b$Changing from train to bus often means walking in heavy rain or strong sun. Covered links make public transport easier to use, at the cost of building and maintaining them.$b$),
 (5,'brazil',$t$Should Brazilian schools open their sports courts to the public at weekends?$t$,$b$Many school courts sit locked on Saturdays and Sundays while neighbourhoods lack places to play. Opening them needs staff, insurance and a plan for cleaning up afterwards.$b$),
 (6,'russia',$t$Should Russian public libraries stay open later in the evening?$t$,$b$Many libraries close before people finish work or school. Longer hours would give students and workers a quiet, warm place to read, but mean more staff hours and heating.$b$),
 (7,'pakistan',$t$Should Pakistani cities build protected cycle lanes near schools and colleges?$t$,$b$Safe lanes would let more students ride instead of relying on crowded transport or lifts. They take road space and money, and only work if drivers respect them.$b$),
 (8,'mexico',$t$Should Mexico City add more public bike-share stations in outer neighbourhoods?$t$,$b$Most stations are in the central districts. Expanding outward would give more people a cheap way to reach work and transit, though outer areas have longer trips and hills.$b$),
 (9,'japan',$t$Should Japanese train stations add more benches on platforms for older passengers?$t$,$b$Japan's population is ageing, and standing for a long wait is hard for many. Benches cost little, but take up platform space on crowded lines at rush hour.$b$),
 (10,'nigeria',$t$Should Lagos add more solar-powered street lights on residential roads?$t$,$b$Dark streets feel unsafe and grid power is unreliable. Solar lights work during outages and have no electricity bill, but panels and batteries need replacing and protecting from theft.$b$),
 (11,'philippines',$t$Should Philippine cities plant more trees along major roads to cool the streets?$t$,$b$Tree-lined roads can be several degrees cooler and make walking pleasant. Trees need space for roots, regular pruning before typhoon season, and care while young.$b$),
 (12,'egypt',$t$Should Cairo restore more of its historic public gardens for families?$t$,$b$Green space per person in Cairo is small, and several old gardens are neglected. Restoring them gives families somewhere to go, but costs money for water, staff and upkeep.$b$),
 (13,'vietnam',$t$Should Hanoi create more pedestrian-only streets on weekend evenings?$t$,$b$The walking streets around Hoan Kiem Lake are popular with families and visitors. More of them would spread the crowds and help small shops, but divert traffic to other roads.$b$),
 (14,'germany',$t$Should German primary schools offer free swimming lessons to every child?$t$,$b$Fewer children can swim safely than a generation ago, partly because pools have closed. Free lessons would help, but need pool time, instructors and transport for classes.$b$),
 (15,'bangladesh',$t$Should Dhaka build more public playgrounds in crowded neighbourhoods?$t$,$b$Many children in dense neighbourhoods have nowhere safe to play. Playgrounds need land, which is scarce and valuable, and someone to keep them clean and safe.$b$),
 (16,'turkey',$t$Should Istanbul run more ferry services at weekends?$t$,$b$Ferries are a cheap, pleasant way to cross the Bosphorus, and weekend boats are often full. More sailings mean more fuel and crew, and space at busy piers.$b$),
 (17,'iran',$t$Should Iranian cities extend metro lines further into the suburbs?$t$,$b$Longer lines would help commuters who now spend hours in traffic and cut air pollution. Metro building is slow and very expensive, and suburbs may use the lines less at first.$b$),
 (18,'united-kingdom',$t$Should UK public libraries lend tools as well as books?$t$,$b$Some libraries already lend drills, ladders and sewing machines. It saves people buying things they use once a year, but needs storage, safety checks and staff time.$b$),
 (19,'thailand',$t$Should Bangkok add more shaded seating at bus stops?$t$,$b$Many stops are just a sign by the road. Shelters with seats make waiting in heat and rain easier, especially for older riders, but take pavement space and need upkeep.$b$),
 (20,'france',$t$Should French towns keep more public toilets open and free?$t$,$b$Free, open toilets matter for older people, families, cyclists and visitors. Towns have to pay for cleaning and repairs, and some worry about damage at night.$b$),
 (21,'italy',$t$Should Italian cities install more drinking fountains in their historic centres?$t$,$b$Rome's nasoni fountains are loved by residents and tourists alike. More fountains would cut bottled-water waste in hot summers, but need pipes laid in old streets and regular testing.$b$),
 (22,'south-africa',$t$Should South African schools teach every child to swim?$t$,$b$Drowning is a leading cause of accidental death for children in many countries. Lessons for every child need pools, trained teachers and transport, which many schools lack.$b$),
 (23,'south-korea',$t$Should Seoul turn more unused rooftops into public gardens?$t$,$b$Rooftop gardens cool buildings, give residents green space and can grow food. They need strong roofs, safe access and someone to look after the plants.$b$),
 (24,'spain',$t$Should Spanish cities plant more street trees to shade pavements in summer?$t$,$b$Summers are getting hotter and shade makes streets usable at midday. Trees need water in dry years and space under pavements, and take years to grow large.$b$),
 (25,'colombia',$t$Should Medellín extend its cable-car lines to more hillside neighbourhoods?$t$,$b$The Metrocable cut long climbs to a few minutes for people living on the steep slopes. New lines are costly, and each has to be planned around homes on the hillsides.$b$)
  ) AS t(ord, forum, title, body)
  JOIN forums f ON f.slug = t.forum;

COMMIT;

-- What you should see: 25 issues, 25 forums used, 0 ballots, 0 test accounts.
SELECT (SELECT count(*) FROM issues) AS issues,
       (SELECT count(DISTINCT forum_id) FROM issues) AS forums_used,
       (SELECT count(*) FROM votes) AS ballots,
       (SELECT count(*) FROM users WHERE email LIKE '%@dd.test') AS test_accounts;
