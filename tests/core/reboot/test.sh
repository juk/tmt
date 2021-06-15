#!/bin/bash
. /usr/share/beakerlib/beakerlib.sh || exit 1

rlJournalStart
    rlPhaseStartSetup
        rlRun "run=\$(mktemp -d)" 0 "Create run directory"
        rlRun "set -o pipefail"
        rlRun "pushd data"
    rlPhaseEnd

    rlPhaseStartTest "Simple reboot test"
        rlRun -s "tmt run -i $run -ddd"
        rlAssertGrep "Rebooting during test /tests/test, reboot count: 0" $rlRun_LOG
        rlAssertGrep "Rebooting during test /tests/test, reboot count: 1" $rlRun_LOG
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "rm -rf output $run" 0 "Remove run directory"
        rlRun "popd"
    rlPhaseEnd
rlJournalEnd
