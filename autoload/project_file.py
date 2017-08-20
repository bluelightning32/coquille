import os
import shlex

ANY_ARG = object()

# [ ([match arg1, match arg2, ...], lambda matched_args: filtered_args) ]
#
# If the lambda is not given, it defaults to lambda l: []
ARG_DEFS = [
        ( ["-h"], ),
        ( ["-help"], ),
        ( ["-no-opt"], ),
        ( ["-byte"], ),
        ( ["-full"], ),
        ( ["-opt"], ),
        ( ["-impredicative-set"], lambda l: ("-impredicative-set",) ),
        ( ["-no-install"], ),
        ( ["-install", ANY_ARG], ),
        ( ["-custom", ANY_ARG, ANY_ARG, ANY_ARG], ),
        ( ["-extra", ANY_ARG, ANY_ARG, ANY_ARG], ),
        ( ["-extra-phony", ANY_ARG, ANY_ARG, ANY_ARG], ),
        ( ["-Q", ANY_ARG, ANY_ARG], lambda l: l ),
        ( ["-I", ANY_ARG], lambda l: l ),
        ( ["-R", ANY_ARG, ANY_ARG], lambda l: l ),
        ( ["-Q"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-R"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-I"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-custom"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-extra"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-extra-phony"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-f", ANY_ARG], lambda l: Exception(l[0] + " not supported") ),
        ( ["-f"], lambda l: Exception(l[0] + " needs an argument") ),
        ( ["-o", ANY_ARG], ),
        ( [ANY_ARG, "=", ANY_ARG], ),
        ( ["-arg", ANY_ARG], lambda l: (l[1],) ),
        ( [ANY_ARG], ),
    ]

def parse_args(args):
    "Filter the args from the project file down to those that go to coqtop"
    result = []
    i = 0
    while i < len(args):
        for d in ARG_DEFS:
            match = True
            j = 0
            while j < len(d[0]):
                if i + j >= len(args):
                    match = False
                    break
                if d[0][j] is ANY_ARG:
                    j += 1
                    continue
                if args[i + j] != d[0][j]:
                    match = False
                    break
                j += 1
            if match:
                if len(d) > 1:
                    filtered = d[1](args[i:i + j])
                    if filtered is Exception:
                        raise filtered
                    else:
                        result.extend(filtered)
                i += j
                break
    return result

def parse_file(project_file):
    "Parse coqtop args from _CoqProject"
    with open(project_file, "r") as f:
        project_args = shlex.split(f.read(), comments=True)
        return parse_args(project_args)

def find_file(source_file):
    "Find the _CoqProject corresponding to a .v source file"
    # Starting with the source buffer's directoy, walk up the tree until
    # one of the directories has a _CoqProject file.
    project_dir = os.path.dirname(source_file)
    while project_dir is not None:
        project_path = os.path.join(project_dir, "_CoqProject")
        if os.path.isfile(project_path):
            break
        else:
            project_path = None
        parent = os.path.join(project_dir, os.pardir)
        if os.path.samefile(parent, project_dir):
            project_dir = None
        else:
            project_dir = parent
    return project_path

def find_and_parse_file(source_file):
    project_file = find_file(source_file)
    if project_file is not None:
        return parse_file(project_file)
    else:
        return []
