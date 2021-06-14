import os
import re
import sys
import textwrap
import time

import click

import tmt
from tmt.steps.execute import TEST_OUTPUT_FILENAME
from tmt.steps.provision import Guest
from tmt.steps.provision.local import GuestLocal


class ExecuteInternal(tmt.steps.execute.ExecutePlugin):
    """
    Use the internal tmt executor to execute tests

    The internal tmt executor runs tests on the guest one by one, shows
    testing progress and supports interactive debugging as well. Test
    result is based on the script exit code (for shell tests) or the
    results file (for beakerlib tests).
    """

    # Supported methods
    _methods = [
        tmt.steps.Method(name='tmt', doc=__doc__, order=50),
        tmt.steps.Method(name='shell.tmt', doc=__doc__, order=80),
        tmt.steps.Method(name='beakerlib.tmt', doc=__doc__, order=80),
        ]

    REBOOT_VARIABLE = 'REBOOT_COUNT'
    REBOOT_SCRIPT_PATHS = ("/usr/bin/rstrnt-reboot", "/usr/bin/rhts-reboot")
    REBOOT_SCRIPT = textwrap.dedent(f"""\
    #!/bin/sh
    if [ -z "${REBOOT_VARIABLE}" ]; then
        export {REBOOT_VARIABLE}=0
    fi
    echo "Requesting reboot: ${REBOOT_VARIABLE}"
    """)

    @classmethod
    def options(cls, how=None):
        """ Prepare command line options for given method """
        options = []
        # Shell script as a test
        options.append(click.option(
            '-s', '--script', metavar='SCRIPT', multiple=True,
            help='Shell script to be executed as a test.'))
        # Interactive mode
        options.append(click.option(
            '-i', '--interactive', is_flag=True,
            help='Run in interactive mode, do not capture output.'))
        # Disable interactive progress bar
        options.append(click.option(
            '--no-progress-bar', is_flag=True,
            help='Disable interactive progress bar showing the current test.'))
        return options + super().options(how)

    def show(self):
        """ Show execute details """
        super().show(['script', 'interactive'])

    def wake(self):
        """ Wake up the plugin (override data with command line) """
        super().wake(options=['script', 'interactive'])
        # Make sure that script is a list
        tmt.utils.listify(self.data, keys=['script'])

    def _show_progress(self, progress, test_name, finish=False):
        """
        Show an interactive progress bar in non-verbose mode.

        If the output is not an interactive terminal, or progress bar is
        disabled using an option, just output the message as info without
        utilising \r. If finish is True, overwrite the previous progress bar.
        """
        # Verbose mode outputs other information, using \r to
        # create a status bar wouldn't work.
        if self.opt('verbose'):
            return

        # No progress if terminal not attached or explicitly disabled
        if not sys.stdout.isatty() or self.opt('no-progress-bar'):
            return

        # For debug mode show just an info message (unless finishing)
        message = f"{test_name} [{progress}]" if not finish else ""
        if self.opt('debug'):
            if not finish:
                self.info(message, shift=1)
            return

        # Show progress bar in an interactive shell.
        try:
            # We need to completely override the previous message, add
            # spaces if necessary.
            message = message.ljust(len(self._previous_progress_message))
        except AttributeError:
            # First iteration, previous message not set
            pass
        self._previous_progress_message = message
        message = self._indent('progress', message, color='cyan')
        sys.stdout.write(f"\r{message}")
        if finish:
            # The progress has been overwritten, return back to the start
            sys.stdout.write(f"\r")
        sys.stdout.flush()

    def execute(self, test, guest, progress):
        """ Run test on the guest """
        # Provide info/debug message
        self._show_progress(progress, test.name)
        self.verbose(
            'test', test.summary or test.name, color='cyan', shift=1, level=2)
        self.debug(f"Execute '{test.name}' as a '{test.framework}' test.")

        # Test will be executed in the workdir
        workdir = os.path.join(
            self.step.plan.discover.workdir, test.path.lstrip('/'))
        self.debug(f"Use workdir '{workdir}'.", level=3)

        # Create data directory, prepare environment
        data_directory = self.data_path(test, full=True, create=True)
        environment = test.environment
        if test.framework == 'beakerlib':
            environment = environment.copy()
            environment['BEAKERLIB_DIR'] = data_directory

        # Prepare custom function to log output in verbose mode
        def log(key, value=None, color=None, shift=1, level=1):
            self.verbose(key, value, color, shift=2, level=3)

        # Execute the test, save the output and return code
        timeout = ''
        start = time.time()
        try:
            stdout = guest.execute(
                test.test, cwd=workdir, env=environment,
                join=True, interactive=self.get('interactive'), log=log,
                timeout=tmt.utils.duration_to_seconds(test.duration))
            test.returncode = 0
        except tmt.utils.RunError as error:
            stdout = error.stdout
            test.returncode = error.returncode
            if test.returncode == tmt.utils.PROCESS_TIMEOUT:
                timeout = ' (timeout)'
                self.debug(f"Test duration '{test.duration}' exceeded.")
        end = time.time()
        self.write(
            self.data_path(test, TEST_OUTPUT_FILENAME, full=True),
            stdout or '', level=3)
        test.real_duration = self.test_duration(start, end)
        duration = click.style(test.real_duration, fg='cyan')
        shift = 1 if self.opt('verbose') < 2 else 2
        self.verbose(
            f"{duration} {test.name} [{progress}]{timeout}", shift=shift)

    def check(self, test):
        """ Check the test result """
        self.debug(f"Check result of '{test.name}'.")
        if test.framework == 'beakerlib':
            return self.check_beakerlib(test)
        else:
            return self.check_shell(test)

    def _setup_reboot(self, guest):
        """ Prepare the guest environment for potential reboot """
        # We only want to change the environment in tmt-provisioned machines,
        # ignore Local and Connect
        if isinstance(guest, GuestLocal) or guest.__class__ is Guest:
            return
        for reboot_file in self.REBOOT_SCRIPT_PATHS:
            self.debug(f"Replacing {reboot_file} with tmt implementation")
            guest.execute(f"echo '{self.REBOOT_SCRIPT}' > {reboot_file}")
            guest.execute(f"chmod +x {reboot_file}")

    def _handle_reboot(self, test, guest):
        """
        Reboot the guest if the test requested it.

        Check the previously fetched test log for signs of reboot request
        and orchestrate the reboot if it was requested. Also increment
        REBOOT_COUNT variable, reset it to 0 if no reboot was requested
        (going forward to the next test). Return whether reboot was done.
        """
        output = self.read(
            self.data_path(test, TEST_OUTPUT_FILENAME, full=True))
        match = re.search(r"Requesting reboot: (?P<count>\d+)", output)
        if match:
            current_count = int(match.group("count"))
            self.debug(f"Rebooting during test {test}, "
                       f"reboot count: {current_count}")
            guest.reboot()
            test.environment[self.REBOOT_VARIABLE] = str(current_count + 1)
            return True
        else:
            test.environment[self.REBOOT_VARIABLE] = "0"
            return False

    def go(self):
        """ Execute available tests """
        super().go()
        self._results = []

        # Nothing to do in dry mode
        if self.opt('dry'):
            self._results = []
            return

        # For each guest execute all tests
        tests = self.prepare_tests()
        for guest in self.step.plan.provision.guests():
            self._setup_reboot(guest)
            # Push workdir to guest and execute tests
            guest.push()
            index = 0
            while index < len(tests):
                test = tests[index]
                self.execute(test, guest, progress=f"{index + 1}/{len(tests)}")
                guest.pull(source=self.data_path(test, full=True))
                if self._handle_reboot(test, guest):
                    continue
                self._results.append(self.check(test))
                index += 1
            # Overwrite the progress bar, the test data is irrelevant
            self._show_progress('', '', True)

    def results(self):
        """ Return test results """
        return self._results

    def requires(self):
        """ Return list of required packages """
        # FIXME Remove when we drop support for the old execution methods
        return ['beakerlib'] if self.step._framework == 'beakerlib' else []
