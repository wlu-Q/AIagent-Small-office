"""Build exact patches for controlled-model and middleware test inputs."""


def add_patch(path, content):
    body = ''.join('+' + line + '\n' for line in content.splitlines())
    return f'*** Begin Patch\n*** Add File: {path}\n{body}*** End Patch'


def update_patch(path, old, new):
    removed = ''.join('-' + line + '\n' for line in old.splitlines())
    added = ''.join('+' + line + '\n' for line in new.splitlines())
    return f'*** Begin Patch\n*** Update File: {path}\n@@\n{removed}{added}*** End Patch'
