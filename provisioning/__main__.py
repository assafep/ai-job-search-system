"""Onboarding lifecycle: plan the contract, or run the implemented hooks."""

import argparse
import sys

from .answers import choose, load_answers
from .lifecycle import ACTIONS, DRIVE_MODES, PROFILES, implementation_gaps, plan, select_setup
from .state import ProvisioningFailure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['plan', 'onboard', 'up', 'start', 'stop', 'status', 'drive', 'drive-login', 'restore-drive-project', 'chatgpt', 'connect'], default='plan')
    parser.add_argument('--setup', choices=PROFILES)
    parser.add_argument('--drive-mode', choices=DRIVE_MODES)
    parser.add_argument('--action', choices=ACTIONS)
    parser.add_argument('--path', help='Override drive_path from deployment/defaults.json')
    parser.add_argument('--applicant', action='store_true', help='Refresh the applicant during onboarding')
    parser.add_argument('--timeout', type=int, default=60, help='Runner start/drain wait in seconds (1–3600)')
    args = parser.parse_args(argv)
    if args.applicant and args.command != 'onboard':
        parser.error('--applicant requires the onboard command')
    if args.command in ('start', 'stop', 'status'):
        from .runner_control import control
        from .state import State
        try:
            with State().locked():
                result = control(args.command, timeout=args.timeout)
        except (ProvisioningFailure, OSError, ValueError) as exc:
            print('Runner control stopped: ' + str(exc), file=sys.stderr)
            return 2
        print(args.command + '_runner: ' + result.outcome.value)
        for line in result.evidence:
            print('  ' + line)
        if result.resume_instruction:
            print('  ' + result.resume_instruction)
        return 0 if result.outcome.value in ('changed', 'unchanged') else 2
    if args.command == 'onboard':
        from .execute import onboard_runtime
        try:
            return onboard_runtime(['--applicant'] if args.applicant else [])
        except (ProvisioningFailure, OSError, ValueError) as exc:
            print('Onboarding stopped: ' + str(exc), file=sys.stderr)
            return 2
    if args.command in ('chatgpt', 'connect'):
        from .chatgpt import prepare
        from .state import State
        steps = [('prepare_chatgpt_connection', prepare)]
        with State().locked():
            for name, action in steps:
                result = action()
                print(name + ': ' + result.outcome.value)
                for line in result.evidence:
                    print('  ' + line)
                if result.resume_instruction:
                    print('  ' + result.resume_instruction)
                if result.outcome.value not in ('changed', 'unchanged'):
                    return 2
        return 0
    if args.command == 'drive-login':
        from .drive_login import main as drive_login_main
        from .state import State
        with State().locked():
            return drive_login_main()
    try:
        answers = load_answers()
    except (ProvisioningFailure, ValueError) as exc:
        parser.error(str(exc))
    args.setup = choose(args.setup, answers, 'setup')
    args.action = choose(args.action, answers, 'action') or 'up'
    args.drive_mode = choose(args.drive_mode, answers, 'drive_mode') or 'user-oauth'
    args.path = choose(args.path, answers, 'drive_path')
    if args.command == 'restore-drive-project':
        from .drive_storage import restore_project
        from .state import State
        if not args.path:
            parser.error('drive_path is missing from deployment/defaults.json')
        try:
            with State().locked():
                result = restore_project(args.path)
        except (ProvisioningFailure, OSError, ValueError) as exc:
            print('Drive project recovery stopped: ' + str(exc), file=sys.stderr)
            return 2
        print('Drive project ' + result['project_id'] + ': ' + result['outcome'])
        return 0
    if args.command == 'drive':
        if not args.path:
            parser.error('drive_path is missing from deployment/defaults.json')
        from .drive_folder import main as drive_main
        from .state import State
        with State().locked():
            return drive_main(['--path', args.path])
    if not args.setup:
        if not sys.stdin.isatty():
            parser.error('--setup is required in non-interactive mode')
        names = tuple(PROFILES)
        print('Choose a setup:')
        for index, name in enumerate(names, 1):
            print('  {}. {}'.format(index, name))
        try:
            choice = input('Setup number: ').strip()
        except (EOFError, KeyboardInterrupt):
            return 130
        if choice not in {str(i) for i in range(1, len(names) + 1)}:
            parser.error('Choose one of the displayed setup numbers')
        args.setup = names[int(choice) - 1]
    setup = select_setup(args.setup, args.drive_mode)
    if args.command == 'up':
        if setup.name != 'local-docker' or args.action != 'up' or setup.drive_mode != 'user-oauth':
            parser.error('live execution currently supports: up --setup local-docker --drive-mode user-oauth')
        from .execute import run_up, ONBOARDING
        print('Reconciling every provisioning/deployment stage; saved stage names never skip verification.')
        try:
            results = run_up(args.path)
        except ProvisioningFailure as exc:
            print('Provisioning stopped: ' + str(exc), file=sys.stderr)
            return 2
        for name, result in results:
            print('{}: {}'.format(name, result.outcome.value))
            for line in result.evidence:
                print('  ' + line)
            if result.resume_instruction:
                print('  resume: ' + result.resume_instruction)
        from .reporting import write_report
        try:
            report = write_report(results, ONBOARDING / 'provisioning-report.pdf')
        except (OSError, ValueError, ImportError) as exc:
            print('Could not write the provisioning PDF report: ' + str(exc), file=sys.stderr)
            return 2
        print('Provisioning report: ' + str(report))
        return 0 if results and all(item.outcome.value in ('changed', 'unchanged') for _, item in results) else 2
    print('Lifecycle contract: {} / {} / {}'.format(setup.name, setup.drive_mode, args.action))
    print('Plan only. No resources, credentials, runtime config or workflows are changed.')
    print('Before execution: validate the whole plan, authorize providers and acquire the installation lock.')
    for index, task in enumerate(plan(setup, args.action), 1):
        print('{}. {} [{}]'.format(index, task.name, task.provider))
        print('   Inputs: {}; output: {}'.format(', '.join(task.inputs), task.output))
        print('   Depends on: {}'.format(', '.join(task.dependencies) or 'preflight / existing bindings'))
    print('Implementation requirements:')
    for gap in implementation_gaps(setup):
        print('  - ' + gap)
    print('Contract and recovery semantics: deployment/lifecycle.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
