-- e2e seed: one PBX backend named pbx1.local reachable at 127.0.0.1:5080 (UDP).
-- Loaded by postgres entrypoint AFTER schema migrations, before kamailio starts
-- (because kamailio depends_on postgres healthy).

\connect kamailio

-- Domain known to Kamailio (so uri==myself for sip:...@pbx1.local works if needed)
INSERT INTO public.domain (domain, did, last_modified)
    VALUES ('pbx1.local', 'pbx1.local', now())
    ON CONFLICT DO NOTHING;
INSERT INTO public.domain (domain, did, last_modified)
    VALUES ('e2e.local', 'e2e.local', now())
    ON CONFLICT DO NOTHING;

-- Dialplan: dpid=1 maps an incoming domain (Request-URI domain part = $rd)
-- to a dispatcher setid. Used by GET_ASTERISK_NODE → dp_replace(1, $rd, ...).
-- Match anything, then the result column is the setid as a string.
INSERT INTO public.dialplan
    (id, dpid, pr, match_op, match_exp, match_len, subst_exp, repl_exp, attrs)
VALUES
    (1, 1, 0, 1, '^pbx1\.local$', 0, '^pbx1\.local$', '1', '');

-- Dispatcher: setid=1 → SIPp UAS on 127.0.0.1:5080 (UDP)
INSERT INTO public.dispatcher
    (id, setid, destination, flags, priority, attrs, description)
VALUES
    (1, 1, 'sip:127.0.0.1:5080', 0, 0, '', 'e2e-pbx1');

-- nethvoice_proxy_routes (used by the NS8 actions UI; harmless here but keeps the
-- domain visible to list-routes / get-route if anyone queries it)
INSERT INTO public.nethvoice_proxy_routes (id, target, route_type, setid)
    VALUES (1, 'pbx1.local', 'domain', 1);

-- Sanity check
SELECT 'routes loaded' AS status,
       (SELECT count(*) FROM public.dispatcher) AS dispatchers,
       (SELECT count(*) FROM public.dialplan) AS dialplans,
       (SELECT count(*) FROM public.domain)   AS domains;
