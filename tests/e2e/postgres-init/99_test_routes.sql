-- e2e seed: routes for phase 1, 2 and 3.
--   Domain routing (dpid=1, matched on $rd):
--     pbx1.local → setid 1 → sip:127.0.0.1:5080   (PBX-1 on UDP 5080)
--     pbx2.local → setid 2 → sip:127.0.0.1:5082   (PBX-2 on UDP 5082)
--   Trunk routing (dpid=2, matched on $ru, fallback when no domain match):
--     ^sip:9[0-9]+@ → setid 3 → sip:127.0.0.1:5080   (carrier DIDs land on PBX-1)
-- Scope: 1 PBX per domain, no load balancing, no failover.

\connect kamailio

INSERT INTO public.domain (domain, did, last_modified) VALUES
    ('pbx1.local', 'pbx1.local', now()),
    ('pbx2.local', 'pbx2.local', now()),
    ('e2e.local',  'e2e.local',  now())
ON CONFLICT DO NOTHING;

-- Dialplan dpid=1: domain → setid (matched against $rd)
INSERT INTO public.dialplan
    (id, dpid, pr, match_op, match_exp, match_len, subst_exp, repl_exp, attrs)
VALUES
    (1, 1, 0, 1, '^pbx1\.local$', 0, '^pbx1\.local$', '1', ''),
    (2, 1, 0, 1, '^pbx2\.local$', 0, '^pbx2\.local$', '2', '');

-- Dialplan dpid=2: trunk pattern → setid (matched against $ru)
-- match_op=1 → regexp; substitution replaces the whole URI with the setid digit.
-- The host part is e2e.local (a known domain) so kamailio's uri==myself check
-- passes; the DID prefix 9xxx selects the trunk route as a dpid=1 fallback.
INSERT INTO public.dialplan
    (id, dpid, pr, match_op, match_exp, match_len, subst_exp, repl_exp, attrs)
VALUES
    (3, 2, 0, 1, '^sip:9[0-9]+@e2e\.local$', 0, '^sip:9[0-9]+@e2e\.local$', '3', '');

-- Dispatcher destinations (one per setid → no LB, no failover)
INSERT INTO public.dispatcher
    (id, setid, destination, flags, priority, attrs, description)
VALUES
    (1, 1, 'sip:127.0.0.1:5080', 0, 0, '', 'e2e-pbx1'),
    (2, 2, 'sip:127.0.0.1:5082', 0, 0, '', 'e2e-pbx2'),
    (3, 3, 'sip:127.0.0.1:5080', 0, 0, '', 'e2e-trunk-to-pbx1');

-- nethvoice_proxy_routes (UI-facing view; kept consistent with the dialplan)
INSERT INTO public.nethvoice_proxy_routes (id, target, route_type, setid) VALUES
    (1, 'pbx1.local',     'domain', 1),
    (2, 'pbx2.local',     'domain', 2),
    (3, '^sip:9[0-9]+@e2e\.local$',  'trunk',  3);

SELECT 'routes loaded' AS status,
       (SELECT count(*) FROM public.dispatcher) AS dispatchers,
       (SELECT count(*) FROM public.dialplan)   AS dialplans,
       (SELECT count(*) FROM public.domain)     AS domains;

