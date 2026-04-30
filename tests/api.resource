*** Settings ***
Library    SSHLibrary
Library    Process
Library    String
Library    OperatingSystem

*** Variables ***
${SIPP_IMAGE}    ${EMPTY}
${SIPP_SCENARIOS_REMOTE}    /tmp/sipp_scenarios

*** Keywords ***
Resolve SIPp image
    [Documentation]    If SIPP_IMAGE is empty, derive it from IMAGE_URL by
    ...    swapping the basename. e.g. ghcr.io/amygos/nethvoice-proxy:e2e
    ...    -> ghcr.io/amygos/sipp:e2e. Falls back to ghcr.io/nethesis/sipp:latest.
    IF    "${SIPP_IMAGE}" != "${EMPTY}"
        RETURN    ${SIPP_IMAGE}
    END
    ${resolved} =    Set Variable    ghcr.io/nethesis/sipp:latest
    TRY
        ${head}    ${tag} =    Split String From Right    ${IMAGE_URL}    :    1
        ${prefix} =    Replace String Using Regexp    ${head}    /[^/]+$    /sipp
        ${resolved} =    Catenate    SEPARATOR=    ${prefix}    :    ${tag}
    EXCEPT
        Log    Could not derive SIPP_IMAGE from IMAGE_URL='${IMAGE_URL}', falling back to ${resolved}
    END
    Set Suite Variable    ${SIPP_IMAGE}    ${resolved}
    RETURN    ${resolved}
Run task
    [Arguments]    ${action}    ${input}    ${decode_json}=${TRUE}    ${rc_expected}=0
    ${stdout}    ${stderr}    ${rc} =     Execute Command    api-cli run ${action} --data '${input}'    return_stdout=True    return_stderr=True    return_rc=True
    Should Be Equal As Integers    ${rc_expected}    ${rc}    Run task ${action} failed!${\n}${stderr}
    IF    ${decode_json} and len($stdout) > 0
        ${response} =    Evaluate    json.loads('''${stdout}''')    modules=json
    ELSE
        ${response} =    Set Variable    ${stdout}
    END
    RETURN    ${response}

Upload SIPp scenarios
    [Documentation]    Push the local tests/sipp_scenarios directory to the
    ...    target node so SIPp containers can mount it. Resolves the
    ...    symlink so the SSH library uploads real files.
    ${scenarios_dir} =    Normalize Path    ${CURDIR}${/}sipp_scenarios
    ${real_dir} =    Evaluate    os.path.realpath(r"${scenarios_dir}")    modules=os
    ${parent_dir} =    Evaluate    os.path.dirname(r"${SIPP_SCENARIOS_REMOTE}")    modules=os
    Execute Command    rm -rf ${SIPP_SCENARIOS_REMOTE} && mkdir -p ${parent_dir}
    Put Directory    ${real_dir}    ${parent_dir}    recursive=True
    ${stdout}    ${rc} =    Execute Command    ls -la ${SIPP_SCENARIOS_REMOTE}
    ...    return_stdout=True    return_rc=True
    Log    Uploaded scenarios:\n${stdout}    console=True
    Should Contain    ${stdout}    uac_basic_call.xml    SIPp scenarios upload failed: ${stdout}

Pull SIPp image on node
    ${image} =    Resolve SIPp image
    ${rc} =    Execute Command    podman pull ${image}    return_stdout=False    return_rc=True
    Should Be Equal As Integers    ${rc}    0    Failed to pull ${image}

Start SIPp UAS on node
    [Documentation]    Run a SIPp UAS in background on the node listening on
    ...    ${local_ip}:${port}. Returns the container name (caller must
    ...    Stop SIPp UAS later).
    [Arguments]    ${local_ip}    ${port}    ${scenario}=uas_answer.xml    ${name}=sipp-uas
    ${image} =    Resolve SIPp image
    Execute Command    podman rm -f ${name}    return_stdout=False    return_rc=True
    ${cmd} =    Catenate    SEPARATOR=${SPACE}
    ...    podman run -d --rm --network host
    ...    --name ${name}
    ...    -v ${SIPP_SCENARIOS_REMOTE}:/scn:ro,z
    ...    ${image}
    ...    -sf /scn/${scenario}
    ...    -i ${local_ip} -p ${port}
    ...    -trace_err -trace_msg -trace_screen
    ...    -message_file /dev/stdout
    ${stdout}    ${stderr}    ${rc} =    Execute Command    ${cmd}
    ...    return_stdout=True    return_stderr=True    return_rc=True
    Should Be Equal As Integers    ${rc}    0    SIPp UAS failed to start: ${stderr}
    Sleep    1s
    RETURN    ${name}

Stop SIPp UAS
    [Arguments]    ${name}=sipp-uas
    Execute Command    podman rm -f ${name}    return_stdout=False    return_rc=True

Run SIPp UAC on node
    [Documentation]    Run a single-call SIPp UAC against ${target} from the
    ...    node. Returns (rc, stdout, stderr).
    [Arguments]    ${target}    ${scenario}=uac_basic_call.xml    ${local_ip}=${EMPTY}
    ...    ${local_port}=5070    ${transport}=udp    ${calls}=1    ${rate}=1
    ...    ${timeout}=15    ${service}=1000    ${target_domain}=${EMPTY}
    ...    ${name}=sipp-uac
    ${i_arg} =    Set Variable If    '${local_ip}' == '${EMPTY}'    ${EMPTY}    -i ${local_ip}
    ${transport_arg} =    Set Variable If    '${transport}' == 'tls'    -t l1
    ...    '${transport}' == 'tcp'    -t t1    ${EMPTY}
    ${tls_arg} =    Set Variable If    '${transport}' == 'tls'
    ...    -tls_cert /scn/dummy.crt -tls_key /scn/dummy.key    ${EMPTY}
    ${domain_key} =    Set Variable If    '${target_domain}' == '${EMPTY}'    ${EMPTY}
    ...    -key target_domain ${target_domain}
    ${image} =    Resolve SIPp image
    Execute Command    podman rm -f ${name}    return_stdout=False    return_rc=True
    ${cmd} =    Catenate    SEPARATOR=${SPACE}
    ...    podman run --rm --network host --name ${name}
    ...    -v ${SIPP_SCENARIOS_REMOTE}:/scn:ro,z
    ...    ${image}
    ...    ${target}
    ...    -sf /scn/${scenario}
    ...    ${i_arg} -p ${local_port}
    ...    ${transport_arg} ${tls_arg}
    ...    -m ${calls} -r ${rate}
    ...    -key service ${service}
    ...    ${domain_key}
    ...    -timeout ${timeout}s
    ...    -trace_err -trace_screen
    ...    -message_file /dev/null
    ${stdout}    ${stderr}    ${rc} =    Execute Command    ${cmd}
    ...    return_stdout=True    return_stderr=True    return_rc=True
    RETURN    ${rc}    ${stdout}    ${stderr}
