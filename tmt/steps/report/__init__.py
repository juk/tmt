import re

import click

import tmt


class Report(tmt.steps.Step):
    """ Provide test results overview and send reports. """

    # Default implementation for report is display
    how = 'display'

    def wake(self):
        """ Wake up the step (process workdir and command line) """
        super().wake()

        # Choose the right plugin and wake it up
        for data in self.data:
            plugin = ReportPlugin.delegate(self, data)
            plugin.wake()
            self._plugins.append(plugin)

        # Nothing more to do if already done
        if self.status() == 'done':
            self.debug(
                'Report wake up complete (already done before).', level=2)
        # Save status and step data (now we know what to do)
        else:
            self.status('todo')
            self.save()

    def show(self):
        """ Show discover details """
        for data in self.data:
            ReportPlugin.delegate(self, data).show()

    def summary(self):
        """ Give a concise report summary """
        summary = tmt.base.Result.summary(self.plan.execute.results())
        self.info('summary', summary, 'green', shift=1)

    def go(self):
        """ Report the guests """
        super().go()

        # Nothing more to do if already done
        if self.status() == 'done':
            self.info('status', 'done', 'green', shift=1)
            self.summary()
            self.try_running_login()
            return

        # Perform the reporting
        for plugin in self.plugins():
            plugin.go()

        # Give a summary, update status and save
        self.summary()
        self.status('done')
        self.save()


class ReportPlugin(tmt.steps.Plugin):
    """ Common parent of report plugins """

    # Default implementation for report is display
    how = 'display'

    # List of all supported methods aggregated from all plugins
    _supported_methods = []

    # Ignore unknown options to produce more user-friendly messages when
    # unsupported report plugins are used along with their options. For example
    #       tmt run report -h html -o
    # would produce an obscure error (Unknown option -o for report) if
    # tmt-report-html wasn't installed. Consume these extra options and
    # process them manually based on --how.
    accept_all_options = True

    @classmethod
    def base_command(cls, method_class=None, usage=None):
        """ Create base click command (common for all report plugins) """

        # Prepare general usage message for the step
        if method_class:
            usage = Report.usage(method_overview=usage)

        # Create the command
        @click.command(cls=method_class, help=usage)
        @click.pass_context
        @click.option(
            '-h', '--how', metavar='METHOD',
            help='Use specified method for results reporting.')
        def report(context, how, **kwargs):
            context.obj.steps.add('report')
            Report._save_context(context)
            # If how is unsupported and there are unknown options, it is OK and
            # will be handled by delegate(). If how is OK and there are unknown
            # options, throw an error.
            methods = [method.name for method in cls.methods()]
            if (not how or how in methods) and context.command.extra_args:
                raise click.NoSuchOption(context.command.extra_args[0])

        return report
