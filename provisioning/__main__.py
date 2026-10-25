"""Onboarding lifecycle: plan the contract, or run the implemented hooks."""

import argparse
import sys

from .answers import choose, load_answers
from .lifecycle import ACTIONS, DRIVE_MODES, PROFILES, implementation_gaps, plan, select_setup
from .state import ProvisioningFailure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['plan', 'up', 'drive', 'drive-login', 'chatgpt', 'connect'], default='plan')
    parser.add_argument('--setup', choices=PROFILES)
    parser.add_argument('--drive-mode', choices=DRIVE_MODES)
    parser.add_argument('--action', choices=ACTIONS)
    parser.add_argument('--path', help='Override drive_path from deployment/defaults.json')
    args = parser.parse_args(argv)
    if args.command in ('chatgpt', 'connect'):
        from .chatgpt import prepare
        from .vercel_deploy import deploy
        from .state import State
        steps = [('prepare_chatgpt_connection', prepare)]
        if args.command == 'connect':
            steps.insert(0, ('deploy_application', deploy))
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
        return drive_login_main()
    try:
        answers = load_answers()
    except (ProvisioningFailure, ValueError) as exc:
        parser.error(str(exc))
    args.setup = choose(args.setup, answers, 'setup')
    args.action = choose(args.action, answers, 'action') or 'up'
    args.drive_mode = choose(args.drive_mode, answers, 'drive_mode') or 'user-oauth'
    args.path = choose(args.path, answers, 'drive_path')
    if args.command == 'drive':
        if not args.path:
            parser.error('drive_path is missing from deployment/defaults.json')
        from .drive_folder import main as drive_main
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
        if setup.name != 'vercel' or args.action != 'up':
            parser.error('live execution currently supports: up --setup vercel')
        from .execute import load_progress, resume_from, run_up, save_progress, up_steps
        completed = load_progress()
        _done, pending = resume_from(up_steps(args.path), completed)
        if _done and pending:
            nxt = pending[0][0]
            if sys.stdin.isatty():
                print('The last run completed: ' + ', '.join(_done))
                try:
                    answer = input('Continue from {}? [Y/n] '.format(nxt)).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return 130
                if answer in ('n', 'no'):
                    completed = []
                    save_progress([])
                    print('Starting from the first stage.')
                else:
                    print('Continuing from ' + nxt + '.')
            else:
                print('Continuing from ' + nxt + '.')
        elif not pending:
            print('Every stage is already recorded. Running them again.')
            completed = []
            save_progress([])
        print('Running implemented hooks. Dependent hooks stop after the first blocked or failed step.')
        if 'provision_drive' in _done and args.path:
            print('Root directory: ' + args.path)
        results = run_up(args.path, completed)
        for name, result in results:
            print('{}: {}'.format(name, result.outcome.value))
            for line in result.evidence:
                print('  ' + line)
            if result.resume_instruction:
                print('  resume: ' + result.resume_instruction)
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
