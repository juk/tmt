# coding: utf-8

""" Common options and the MethodCommand class """

import re

import click

# Verbose, debug and quiet output
verbose_debug_quiet = [
    click.option(
        '-v', '--verbose', count=True, default=0,
        help='Show more details. Use multiple times to raise verbosity.'),
    click.option(
        '-d', '--debug', count=True, default=0,
        help='Provide debugging information. Repeat to see more details.'),
    click.option(
        '-q', '--quiet', is_flag=True,
        help='Be quiet. Exit code is just enough for me.'),
    ]

# Force and dry actions
force_dry = [
    click.option(
        '-f', '--force', is_flag=True,
        help='Overwrite existing files and step data.'),
    click.option(
        '-n', '--dry', is_flag=True,
        help='Run in dry mode. No changes, please.'),
    ]

# How option names
HOW = ['-h', '--how']


def is_long_opt(option):
    """ Check if an option is long """
    prefix, _ = click.parser.split_opt(option)
    return len(prefix) > 1


def create_method_class(methods, accept_all=False):
    """
    Create special class to handle different options for each method

    Accepts dictionary with method names and corresponding commands:
    For example: {'fmf', <click.core.Command object at 0x7f3fe04fded0>}
    Methods should be already sorted according to their priority.

    If accept_all is set to True, the command parser will consume all
    options and won't throw an error on undefined options. These options
    will be stored in extra_args attribute to be processed later.
    """

    class MethodCommand(click.Command):
        _method = None

        def _check_method(self, args):
            """ Manually parse the --how option """
            how = None

            for index in range(len(args)):
                # Handle '--how method' or '-h method'
                if args[index] in HOW:
                    try:
                        how = args[index + 1]
                    except IndexError:
                        pass
                    break
                # Handle '--how=method'
                elif args[index].startswith('--how='):
                    how = re.sub('^--how=', '', args[index])
                    break
                # Handle '-hmethod'
                elif args[index].startswith('-h'):
                    how = re.sub('^-h ?', '', args[index])
                    break

            # Find method with the first matching prefix
            if how is not None:
                for method in methods:
                    if method.startswith(how):
                        self._method = methods[method]
                        break

        def parse_args(self, context, args):
            self._check_method(args)
            if self._method is not None:
                return self._method.parse_args(context, args)
            return super().parse_args(context, args)

        def get_help(self, context):
            if self._method is not None:
                return self._method.get_help(context)
            return super().get_help(context)

        def invoke(self, context):
            if self._method:
                return self._method.invoke(context)
            return super().invoke(context)

    class AcceptAllMethodCommand(MethodCommand):
        def __init__(self, **kwargs):
            super(AcceptAllMethodCommand, self).__init__(**kwargs)
            self.extra_args = []

        def make_parser(self, ctx):
            """
            Modify how the parser is created

            Inspired by: https://stackoverflow.com/a/54077611 , made
            more robust for short option handling and manual --how processing.
            """
            parser = super(MethodCommand, self).make_parser(ctx)
            command = self
            # Store the extra arguments for later checking
            extra_args = self.extra_args

            class AcceptAllDict(dict):
                """
                A dictionary that on member check adds an option if it
                does not exist causing the parser to accept unknown options.
                """
                @staticmethod
                def _split_opt(opt):
                    prefix, value = click.parser.split_opt(opt)
                    if len(prefix) == 1:
                        # Short options, may be combined, e.g. run -vvdda
                        return [prefix + short_opt for short_opt in value]
                    else:
                        # Long opt, return as it is
                        return [opt]

                def __contains__(self, opt):
                    result = True
                    for item in self._split_opt(opt):
                        # Ignore --how, we process it manually
                        if item not in HOW:
                            # Trivially check dict to avoid infinite recursion
                            in_short = super(
                                AcceptAllDict,
                                parser._short_opt).__contains__(item)
                            in_long = super(
                                AcceptAllDict,
                                parser._long_opt).__contains__(item)
                            if not in_short and not in_long:
                                # We don't know the option, add it
                                name = item.lstrip('-')
                                click.option(item)(command)
                                option = command.params[-1]
                                parser.add_option(
                                    option, [item], name.replace('-', '_'))
                                # Save the option so that we know it was
                                # specially added
                                extra_args.append(item)
                            # This will be used when matching long options,
                            # return False for short options to avoid errors
                            if not is_long_opt(item):
                                result = False
                        else:
                            result = False
                    return result

            # Make use of the accepting dict for both short and long options
            parser._short_opt = AcceptAllDict(parser._short_opt)
            parser._long_opt = AcceptAllDict(parser._long_opt)
            return parser

    return AcceptAllMethodCommand if accept_all else MethodCommand
