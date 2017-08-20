Coquille
========

[![Build Status](https://travis-ci.org/the-lambda-church/coquille.svg?branch=pathogen-bundle)](https://travis-ci.org/the-lambda-church/coquille)

Coquille is a vim plugin aiming to bring the interactivity of CoqIDE into your
favorite editor.

Installation
------------

This repository is meant to be used as a [pathogen][1] bundle. If you don't
already use pathogen, I strongly recommend that you start right now.

As everybody knows, vim is a wonderful editor which offers no way for a plugin
to track modifications on a buffer. For that reason Coquille depends on a set of
heuristicts collected in [vimbufsync][2] to detect modifications in the buffer.
You will need to make this plugin available in your runtime path (it can be
installed as a pathogen bundle as well) if you want Coquille to work.

Once that is done, installing Coquille is just as simple as doing :

    cd ~/.vim/bundle
    git clone https://github.com/trefis/coquille.git

Not that by default, you will be in the `pathogen-bundle` branch, which also
ships Vincent Aravantinos [syntax][3] and [indent][4] scripts for Coq, as well
as an ftdetect script.
If you already have those in your vim config, then just switch to the master
branch.

Getting started
---------------

When a coq type file is loaded (anything with a .v extension), these commands become available:
- CoqLaunch {coqtop arg} ..
- Coq {vernacular command} ..
- CoqNext
- CoqToCursor
- CoqUndo
- CoqKill

By default Coquille forces no mapping for these commands, however two sets of
mapping are already defined and you can activate them by adding :

    " Maps Coquille commands to CoqIDE default key bindings
    au FileType coq call coquille#CoqideMapping()

or

    " Maps Coquille commands to <F2> (Undo), <F3> (Next), <F4> (ToCursor)
    au FileType coq call coquille#FNMapping()

to your `.vimrc`.

Alternatively you can, of course, define your own.

Starting Coq
------------

Coquille will implicitly start coqtop when any of its commands are run.
Coquille will parse the \_CoqProject for options to pass to coqtop. This
behavior can be disabled by setting `g:coquille_append_project_args` to 0.

Additional arguments for coqtop can be specified by explicitly starting coqtop
using `:CoqLaunch`.

Multiple files
--------------

Coquille supports having multiple coq source files (.v files) open at the same
time. The coq source files can be in separate tabs, different windows (splits)
within the same tab, or switching between hidden buffers in the same window.

When coquille is first started in a tab, it will create an info panel and a
goals panel (split). By default if more source windows are added to the same
tab, coquille will reuse the existing info and goals panels for the new source
windows. Whenever the active source window is switched, the corresponding info
and goals buffers will be displayed in their panels -- hiding the previous
info and goals buffers.

Alternatively `g:coquilled_shared` can be set to 0, which tells coquille to
create separate info and goals panels for each coq source window. It can also
be changed on a per window basis by setting `w:coquille_shared`.

Running query commands
----------------------

You can run an arbitrary query command (that is `Check`, `Print`, etc.) by
calling `:Coq MyCommand foo bar baz.` and the result will be displayed in the
Infos panel.

Configuration
-------------

Note that the color of the "lock zone" is hard coded and might not be pretty in
your specific setup (depending on your terminal, colorscheme, etc).
To change it, you can overwrite the `CheckedByCoq`, `SentToCoq`, `CoqError`,
and `CoqWarning` highlight groups (`:h hi` and `:h highlight-groups`) to colors
that works better for you.
See [coquille.vim][5] for an example.

You can set the following variable to modify Coquille's behavior:

    g:coquille_auto_move            Set it to 'true' if you want Coquille to
        (default = 'false')         move your cursor to the end of the lock zone
                                    after calls to CoqNext or CoqUndo

    g:coquille_shared               Set it to 0 to cause Coquille to create new
        (default = 1)               info and goals panels for each source
                                    window in a tab.

    g:coquille_append_project_args  When set to non-zero, \_CoqProject is parsed
        (default = 1)               for args to pass to coqtop.

    g:coquille_keep_open            Set it to 1 to prevent Coquille from
        (default = 0)               closing the info and goals panels when they
                                    are no longer referenced by any coq source
                                    windows. This is useful to prevent the
                                    window layout from changing when a non-coq
                                    source file is edited in the coq source
                                    window and the coq source file is not
                                    hidden.

Python version
--------------

Coquille requires python 2 or python 3 support in vim. If both are available,
Coquille will use python 3. Once Coquille uses python 3, that prevents the vim
from using python 2 in any other plugins. You can force Coquille to use python
2 by putting `:call has('python')` in your `.vimrc`.

Screenshots
------------

Because pictures are always the best sellers :

![Coquille at use](http://the-lambda-church.github.io/coquille/coquille.png)

[1]: https://github.com/tpope/vim-pathogen
[2]: https://github.com/def-lkb/vimbufsync
[3]: http://www.vim.org/scripts/script.php?script_id=2063 "coq syntax on vim.org"
[4]: http://www.vim.org/scripts/script.php?script_id=2079 "coq indent on vim.org"
[5]: https://github.com/the-lambda-church/coquille/blob/master/autoload/coquille.vim#L813
