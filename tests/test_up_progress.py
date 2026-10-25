from provisioning.execute import resume_from


def test_resume_keeps_only_a_leading_prefix():
    steps = (('a', None), ('b', None), ('c', None))
    done, pending = resume_from(steps, ['a', 'c'])
    assert done == ['a']
    assert [name for name, _action in pending] == ['b', 'c']


def test_resume_from_the_start_when_nothing_completed():
    steps = (('a', None), ('b', None))
    done, pending = resume_from(steps, [])
    assert done == []
    assert pending == list(steps)


if __name__ == '__main__':
    test_resume_keeps_only_a_leading_prefix()
    test_resume_from_the_start_when_nothing_completed()
    print('ok')
