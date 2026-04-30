*** Settings ***
Documentation    SIPp smoke tests on a real NS8 node.
...    Verifies that the published SIPp helper image runs on the target
...    node and that kamailio is reachable on the standard SIP UDP port.
...    Heavier call-routing scenarios are exercised by the local pytest
...    e2e suite under tests/e2e/.
Library    SSHLibrary
Resource    ./api.resource
Suite Setup    Run Keywords    Pull SIPp image on node    AND    Upload SIPp scenarios

*** Test Cases ***
SIPp container can run on the node
    [Documentation]    Trivial sanity check: the just-pulled SIPp image
    ...    actually executes inside podman on the node. SIPp prints its
    ...    usage on -h and exits with 99 ("Normal exit without calls").
    ${stdout}    ${stderr}    ${rc} =    Execute Command
    ...    podman run --rm ${SIPP_IMAGE} -h
    ...    return_stdout=True    return_stderr=True    return_rc=True
    Should Be True    ${rc} in [0, 99]    SIPp -h failed (rc=${rc}): ${stderr}
    Should Contain    ${stdout}${stderr}    sipp

SIPp can reach kamailio on UDP 5060
    [Documentation]    Send a single INVITE to an unknown domain. Kamailio
    ...    will respond with a 4xx (because no route matches), and SIPp
    ...    will exit 1 ("test ended with errors"). Both prove that the
    ...    container can talk to the proxy. We additionally verify the
    ...    final-statistics block to make sure SIPp completed normally.
    ${rc}    ${stdout}    ${stderr} =    Run SIPp UAC on node
    ...    target=${local_ip}:5060
    ...    scenario=uac_basic_call.xml
    ...    target_domain=unknown.invalid
    ...    timeout=10
    Should Not Be Equal As Integers    ${rc}    125    SIPp image not available
    Should Not Be Equal As Integers    ${rc}    255    SIPp crashed: ${stderr}
    Should Not Contain    ${stderr}    Couldn't open socket
    Should Contain Any    ${stdout}${stderr}    Unexpected message
    ...    Aborting call on unexpected message    Failed call    Successful call
