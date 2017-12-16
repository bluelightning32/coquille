" When set to 1, new coq source windows will try to use a info and goals
" window in the same tab. When set to 0, they will create their own info and
" goals windows.
"
" This can be overriden in a source window by setting w:coquille_shared.
let g:coquille_shared = 1
" When set to 1, keep the infos and goals windows open after they are no
" longer referenced. This is useful if you arranged the coq windows in a
" specific way, and you keep switching between coq and non-coq files without
" using the hidden option.
"
" This can be overriden in a source window by setting w:coquille_keep_open.
let g:coquille_keep_open = 0
" When set to 1, read the coqtop arguments from _CoqProject, and append them to
" the arguments passed by CoqLaunch when calling coqtop.
"
" This can be overriden in a source buffer by setting
" b:coquille_append_project_args.
let g:coquille_append_project_args = 1

let s:current_dir=expand("<sfile>:p:h") 
let s:next_winid = 1
let s:active_winid = -1
let s:active_tabnr = -1
let s:active_winnr = -1

if !exists('coquille_auto_move')
    let g:coquille_auto_move="false"
endif

" Return true if win_getid works
function! coquille#Test_win_getid()
    if !exists("*win_getid")
        return 0
    endif

    " At least the function exists.
    "
    " Fedora 23 has a broken version that only works if the tab argument is
    " left off. When the tab argument is included, it always returns 0.
    "
    " So test win_getid() by trying to get the id of the current window.
    let s:curid = win_getid(tabpagenr(), winnr())
    if s:curid == 0 || s:curid == -1
        return 0
    endif
    " All tests passed. The system win_getid() function works.
    return 1
endfunction

" Cache whether win_getid works
let s:win_getid_works = coquille#Test_win_getid()

function! coquille#Python(cmd)
    if has('python3')
        execute 'python3' a:cmd
    else
        execute 'python' a:cmd
    endif
endfunction

function! coquille#PythonExpr(expr)
    let l:ret = 0
    if has('python3')
        execute 'python3 vim.command("let l:ret = " + coquille.vim_repr(' a:expr '))'
    else
        execute 'python vim.command("let l:ret = " + coquille.vim_repr(' a:expr '))'
    endif
    return l:ret
endfunction

try
    call coquille#Python('import sys, vim')
catch /E319:/
    if !exists('s:warned')
        echo "vim doesn't support python. Turn off coquille"
        let s:warned = 1
    endif
    function! coquille#Register()
    endfunction
    finish
endtry

" Load vimbufsync if not already done
call vimbufsync#init()

call coquille#Python('if not vim.eval("s:current_dir") in sys.path:'.
            \'sys.path.append(vim.eval("s:current_dir"))')
call coquille#Python('import coquille')

function! coquille#FNMapping()
    "" --- Function keys bindings
    "" Works under all tested config.
    map <buffer> <silent> <F2> :CoqUndo<CR>
    map <buffer> <silent> <F3> :CoqNext<CR>
    map <buffer> <silent> <F4> :CoqToCursor<CR>

    imap <buffer> <silent> <F2> <C-\><C-o>:CoqUndo<CR>
    imap <buffer> <silent> <F3> <C-\><C-o>:CoqNext<CR>
    imap <buffer> <silent> <F4> <C-\><C-o>:CoqToCursor<CR>
endfunction

function! coquille#CoqideMapping()
    "" ---  CoqIde key bindings
    "" Unreliable: doesn't work with all terminals, doesn't work through tmux,
    ""  etc.
    map <buffer> <silent> <C-A-Up>    :CoqUndo<CR>
    map <buffer> <silent> <C-A-Left>  :CoqToCursor<CR>
    map <buffer> <silent> <C-A-Down>  :CoqNext<CR>
    map <buffer> <silent> <C-A-Right> :CoqToCursor<CR>

    imap <buffer> <silent> <C-A-Up>    <C-\><C-o>:CoqUndo<CR>
    imap <buffer> <silent> <C-A-Left>  <C-\><C-o>:CoqToCursor<CR>
    imap <buffer> <silent> <C-A-Down>  <C-\><C-o>:CoqNext<CR>
    imap <buffer> <silent> <C-A-Right> <C-\><C-o>:CoqToCursor<CR>
endfunction

" Create a new, unlisted, unloaded buffer, with a name starting with base_name
function! coquille#CreateNewBuffer(base_name)
    if !bufexists(a:base_name)
        return bufnr(a:base_name, 1)
    endif
    let l:suffix = 1
    while bufexists(a:base_name . l:suffix)
        let l:suffix += 1
    endwhile
    return bufnr(a:base_name . l:suffix, 1)
endfunction

" Detach any goal and info buffer attached the coq source buffer.
function! coquille#DetachSupportingBuffers(bufid)
    for type in ["goal", "info"]
        let l:varname = "coquille_" . type . "_bufid"
        let l:support_bufid = getbufvar(a:bufid, l:varname, -1)
        if bufexists(l:support_bufid)
            call setbufvar(l:support_bufid, "&bufhidden", "delete")
        endif
        call setbufvar(a:bufid, l:varname, -1)
    endfor
    call coquille#Python('coquille.BufferState.lookup_bufid(' . a:bufid . ').' .
                \ 'kill_coqtop()')
endfunction

function! coquille#KillSession()
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#TabWinBufnr(tabpagenr(), winnr())

    call coquille#DetachSupportingBuffers(l:bufid)

    " Reactive the coq source window now that its current buffer is no longer
    " a coq source. This will close the support windows if nothing else is
    " referencing them.
    call coquille#WindowActivated(l:winid, tabpagenr(), winnr())

    setlocal ei=InsertEnter
endfunction

" If they do not already exist, create a goals and infos buffer for coq source,
" bufid.
function! coquille#OpenSupportingBuffers(bufid, ...)
    if !bufloaded(a:bufid)
        return
    endif
    if getbufvar(a:bufid, "&filetype") != "coq"
        return
    endif
    let l:created_bufs = 0
    for type in ["goal", "info"]
        " Skip this buffer type if b:coquille_goal_bufid/b:coquille_info_bufid
        " is set on the coq source buffer, and it points to a loaded buffer
        let l:varname = "coquille_" . type . "_bufid"
        let l:support_bufid = getbufvar(a:bufid, l:varname, -1)
        if bufexists(l:support_bufid)
            continue
        endif

        if type == "goal"
            let l:bufname_base = "Goals: ".bufname("%")
        else
            let l:bufname_base = "Infos: ".bufname("%")
        endif
        let l:support_bufid = coquille#CreateNewBuffer(l:bufname_base)

        " In the support window, do (setlocal nomodifiable). This prevents the
        " user from accidentally editing the contents.
        call setbufvar(l:support_bufid, "&modifiable", 0)
        " In the support window, do (setlocal buftype=nofile). This way even
        " if the buffer is modified, the modified option won't get set.
        call setbufvar(l:support_bufid, "&buftype", "nofile")
        " In the support window, set the filetype directly, and store it to
        " coquille_filetype. If the filefile type gets overwritten later, the
        " file type detector will restore it from b:coquille_filetype.
        let l:filetype = "coq-".type."s"
        call setbufvar(l:support_bufid, "coquille_filetype", l:filetype)
        call setbufvar(l:support_bufid, "&filetype", l:filetype)
        " In the support window, do (setlocal noswapfile). This way if vim
        " exits uncleanly, on the next start up the user won't be asked to
        " restore the info/goals files.
        call setbufvar(l:support_bufid, "&swapfile", 0)
        call setbufvar(l:support_bufid, "&bufhidden", "hide")
        call setbufvar(l:support_bufid, "coquille_source_bufid", a:bufid)
        " Add it to the buffer list
        call setbufvar(l:support_bufid, "&buflisted", 1)

        " Set b:coquille_goal_bufid/b:coquille_info_bufid on the coq source
        " buffer.
        call setbufvar(a:bufid, l:varname, l:support_bufid)
        let l:created_bufs += 1
    endfor
    if l:created_bufs
        call setbufvar(a:bufid, "checked", -1)
        call setbufvar(a:bufid, "sent", -1)
        call setbufvar(a:bufid, "errors", -1)
        execute "autocmd BufUnload <buffer=" . a:bufid .
                    \ "> call coquille#DetachSupportingBuffers(". a:bufid .")"
        " Automatically sync the buffer when the cursor moves while in insert
        " mode. Typically this happens when the buffer is modified. Syncing
        " the buffer is useful when we edit the portion of the buffer which
        " has already been sent to coq, we can then rewind to the appropriate
        " point.  It's still incomplete though, the plugin won't sync when you
        " undo or delete some part of your buffer. So the highlighting will be
        " wrong, but nothing really problematic will happen, as sync will be
        " called the next time you explicitly call a command (be it 'rewind'
        " or 'interp')
        execute "autocmd CursorMovedI <buffer=" . a:bufid .
                    \ "> call coquille#Python('"
                    \ "coquille.BufferState.lookup_bufid(".
                    \ a:bufid . ").sync()')"
        " initialize the plugin (launch coqtop)
        let l:result = coquille#PythonExpr(
                    \ 'coquille.BufferState.lookup_bufid(' .
                    \ a:bufid . ').launch_coq(' .
                    \ '*' . string(map(copy(a:000), 'expand(v:val)')) . ')')
        if !l:result
            " Since coq failed to launch, delete the buffers created above
            execute "bdelete" getbufvar(a:bufid, "coquille_goal_bufid")
                        \ getbufvar(a:bufid, "coquille_info_bufid")
            call coquille#DetachSupportingBuffers(a:bufid)
            return 0
        endif
    endif
    return 1
endfunction

function! coquille#WinGetId(tabnr, winnr)
    if s:win_getid_works
        return win_getid(a:winnr, a:tabnr)
    endif
    let l:winid = gettabwinvar(a:tabnr, a:winnr, "coquille_winid", -1)
    if l:winid != -1
        return l:winid
    endif
    let l:winid = s:next_winid
    let s:next_winid += 1
    call settabwinvar(a:tabnr, a:winnr, "coquille_winid", l:winid)
    return l:winid
endfunction

" Return the [tabnr, winnr] for a given window id.
"
" If the window id cannot be found, [0, 0] is returned.
"
" Passing a close value in [hint_tabnr, winnr] speeds up the search
function! coquille#WinId2TabWin(winid, hint_tabnr, hint_winnr)
    " win_id2tabwin was introduced in vim 8. Use it if it is available.
    if s:win_getid_works
        return win_id2tabwin(a:winid)
    endif
    if gettabwinvar(a:hint_tabnr, a:hint_winnr, "coquille_winid", -1) == a:winid
        " If this matches and the hint_tabnr or hint_winnr is 0, that means
        " the current tab/window is a match. However the caller is expecting
        " the real tab and window numbers, so they have to be converted.
        let l:tabnr = a:hint_tabnr
        if l:tabnr == 0
            let l:tabnr = tabpagenr()
        endif
        let l:winnr = a:hint_winnr
        if l:winnr == 0
            let l:winnr = winnr()
        endif
        return [l:tabnr, l:winnr]
    endif
    let l:search_tab = 1
    while l:search_tab <= tabpagenr("$")
        let l:search_win = 1
        while l:search_win <= tabpagewinnr(l:search_tab, "$")
            if gettabwinvar(l:search_tab, l:search_win, "coquille_winid", -1)
                        \ == a:winid
                return [l:search_tab, l:search_win]
            endif
            let l:search_win += 1
        endwhile
        let l:search_tab += 1
    endwhile
    return [0, 0]
endfunction

" Return the window variable in the given window id, or default if the window
" or variable does not exist
"
" Specifying a hint_tabnr and hint_winnr that matches the winid speeds up the
" search.
function! coquille#GetWinVar(winid, hint_tabnr, hint_winnr, varname, def)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    return gettabwinvar(l:tabwin[0], l:tabwin[1], a:varname, a:def)
endfunction

" Set the window variable in the given window id to the given value, if the
" window exists
"
" Specifying a hint_tabnr and hint_winnr that matches the winid speeds up the
" search.
function! coquille#SetWinVar(winid, hint_tabnr, hint_winnr, varname, value)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    call settabwinvar(l:tabwin[0], l:tabwin[1], a:varname, a:value)
endfunction

" Return true if the window is open
function! coquille#WinIdExists(winid, hint_tabnr, hint_winnr)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    return l:tabwin[0] != 0 || l:tabwin[1] != 0
endfunction

function! coquille#WinVarExists(winid, hint_tabnr, hint_winnr, varname)
    let l:value1 = coquille#GetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                 \                    a:varname, -1)
    let l:value2 = coquille#GetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                 \                    a:varname, -2)
    return l:value1 == l:value2
endfunction

function! coquille#CloseWindow(winid, hint_tabnr, hint_winnr)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    " Only try close the window if it exists
    if l:tabwin[0] != 0 || l:tabwin[1] != 0
        let l:close_bufid = coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
        " The only way to close a window in a different tab is to switch to
        " it. Save the current tab so it can be restored later.
        let l:cur_tab = tabpagenr()
        let l:cur_win = winnr()
        let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
        " Switch to the tab
        execute l:tabwin[0] . " tabnext"
        " Vim uses the bufhidden variable from the active buffer. Really it
        " should use the bufhidden variable from the window that is getting
        " closed. So as a workaround, temporarily copy the value from the
        " closing buffer to the active buffer.
        let l:cur_bufid = bufnr("")
        let l:bufhidden_backup = &bufhidden
        let &bufhidden = getbufvar(l:close_bufid, "&bufhidden")
        " Quit the window
        execute l:tabwin[1] . " wincmd q"
        " Switch back to the original tab
        call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
        " Restore the old value of the bufhidden variable
        call setbufvar(l:cur_bufid, "&bufhidden", l:bufhidden_backup)
    endif
endfunction

" Make winid current by changing the current tab and window within that tab
function! coquille#WinGoToId(winid, hint_tabnr, hint_winnr)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    if l:tabwin[0] == 0 && l:tabwin[1] == 0
        return 0
    else
        execute l:tabwin[0] . " tabnext"
        execute l:tabwin[1] . " wincmd w"
        return 1
    endif
endfunction

" Return the active buffer number for the given tab and window
function! coquille#TabWinBufnr(tabnr, winnr)
    return tabpagebuflist(a:tabnr)[a:winnr - 1]
endfunction

" Return the active buffer number for the given winid
function! coquille#WinidBufnr(winid)
    if s:win_getid_works
        return getwininfo(a:winid)[0].bufnr
    endif
    let l:tabwin = coquille#WinId2TabWin(a:winid, 0, 0)
    return coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
endfunction

" Load the given buffer in the given window
function! coquille#SetWinBufnr(winid, hint_tabnr, hint_winnr, bufid)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    let l:target_bufid = coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
    if a:bufid == l:target_bufid
        " The buffer is already active in the target window
        return
    endif
    let l:cur_tab = tabpagenr()
    let l:cur_win = winnr()
    let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
    " Go to the target window
    call coquille#WinGoToId(a:winid, l:tabwin[0], l:tabwin[1])
    execute a:bufid . " buffer"
    " Restore the previous active window
    call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
endfunction

" Return the list of all windows that are showing bufid as a list of winids
function! coquille#WinFindBuf(bufid)
    if s:win_getid_works
        return win_findbuf(a:bufid)
    endif
    let winlist = []
    for tabnr in range(1, tabpagenr('$'))
        let l:tabwins = tabpagebuflist(l:tabnr)
        for i in range(len(l:tabwins))
            let l:winnr = i + 1
            if a:bufid == l:tabwins[i]
                call add(l:winlist, coquille#WinGetId(l:tabnr, l:winnr))
            endif
        endfor
    endfor
    return l:winlist
endfunction

" Return the window id of a window in the specified tab with the specified
" filetype, with w:coquille_shared set to 1, or -1 if no such window exists
function! coquille#FindSharedWin(tabnr, filetype)
    let l:search_win = 1
    let l:default_shared = !exists("g:coquille_shared") || g:coquille_shared
    while l:search_win <= tabpagewinnr(a:tabnr, "$")
        if !gettabwinvar(a:tabnr, l:search_win, "coquille_shared",
                    \    l:default_shared)
            let l:search_win += 1
            continue
        endif
        let bufid = coquille#TabWinBufnr(a:tabnr, l:search_win)
        if getbufvar(l:bufid, "&filetype") != a:filetype
            let l:search_win += 1
            continue
        endif
        return coquille#WinGetId(a:tabnr, l:search_win)
    endwhile
    return -1
endfunction

function coquille#LoadBlankBuffer(winid, hint_tabnr, hint_winnr)
    let l:cur_tab = tabpagenr()
    let l:cur_win = winnr()
    let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
    " Go to the target window
    call coquille#WinGoToId(a:winid, a:hint_tabnr, a:hint_winnr)
    enew
    setlocal buftype=nofile
    setlocal noswapfile
    " Restore the previous active window
    call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
endfunction

" Close the info/goal window if coquille_keep_open is false and there are no
" more source windows referencing the support window in the tab
"
" If the window says open, load an empty buffer in it
function coquille#GarbageCollectSupportWin(winid)
    let l:tabwin = coquille#WinId2TabWin(a:winid, 0, 0)
    if l:tabwin[0] == 0 && l:tabwin[1] == 0
        " The support window doesn't exist
        return
    endif
    let l:def_keep_open = 0
    if exists("g:coquille_keep_open")
        let l:def_keep_open = g:coquille_keep_open
    endif
    if coquille#GetWinVar(a:winid, l:tabwin[0], l:tabwin[1],
                \         "coquille_keep_open", l:def_keep_open)
        call coquille#LoadBlankBuffer(a:winid, l:tabwin[0], l:tabwin[1])
        return
    endif
    let l:search_win = 1
    while l:search_win <= tabpagewinnr(l:tabwin[0], "$")
        if gettabwinvar(l:tabwin[0], l:search_win, "coquille_goal_winid", -1)
                    \ == a:winid ||
                    \ gettabwinvar(l:tabwin[0], l:search_win,
                    \              "coquille_info_winid", -1)
                    \ == a:winid
            " Found a source window that still references the support window.
            " Now see if that source window has a source buffer.
            let l:search_bufid = coquille#TabWinBufnr(l:tabwin[0], l:search_win)
            let l:search_goal_bufid = getbufvar(l:search_bufid,
                        \                       "coquille_goal_bufid", -1)
            let l:search_info_bufid = getbufvar(l:search_bufid,
                        \                       "coquille_info_bufid", -1)
            if bufexists(l:search_goal_bufid) && bufexists(l:search_info_bufid)
                " The window can't be closed because the source buffer is
                " still using it. However that source buffer is not active, so
                " put a blank buffer in the support window instead.
                call coquille#LoadBlankBuffer(a:winid, l:tabwin[0], l:tabwin[1])
                return
            endif
        endif
        let l:search_win += 1
    endwhile
    call coquille#CloseWindow(a:winid, l:tabwin[0], l:tabwin[1])
endfunction

" Create/swap buffers/close the goals and info windows attached to a coq
" source window.
function! coquille#UpdateSupportingWindows(winid, hint_tabnr, hint_winnr)
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    let l:bufid = coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
    let l:goal_winid = coquille#GetWinVar(a:winid, l:tabwin[0],
                \                         l:tabwin[1],
                \                         "coquille_goal_winid", -1)
    let l:info_winid = coquille#GetWinVar(a:winid, l:tabwin[0],
                \                         l:tabwin[1],
                \                         "coquille_info_winid", -1)
    if getbufvar(l:bufid, "coquille_block_activate", 0)
        " An autocmd recursively called this function while the window was
        " already getting activated
        return
    endif
    call setbufvar(l:bufid, "coquille_block_activate", 1)

    let l:goal_bufid = getbufvar(l:bufid, "coquille_goal_bufid", -1)
    let l:info_bufid = getbufvar(l:bufid, "coquille_info_bufid", -1)
    if l:goal_bufid != -1 && l:info_bufid != -1
        " The requested window has a coq source buffer. If the source has
        " corresponding info and goal buffers, make sure they are displayed in
        " the goal and info windows. The goal and info windows are created if
        " necessary.
        if !coquille#WinIdExists(l:goal_winid, 0, 0)
            let l:goal_winid = -1
        endif
        if !coquille#WinIdExists(l:info_winid, 0, 0)
            let l:info_winid = -1
        endif
        " Creating new windows involves switching the current window and tab.
        " Save the current values so they can be restored at the end
        let l:cur_tab = tabpagenr()
        let l:cur_win = winnr()
        let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)

        if l:goal_winid == -1
            " See if there is a shared goals window in the current tab that
            " can be used
            let l:goal_winid = coquille#FindSharedWin(l:tabwin[0], "coq-goals")
            if l:goal_winid != -1
                call coquille#SetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                            \           "coquille_goal_winid", l:goal_winid)
            endif
        endif
        if l:goal_winid == -1
            let l:goal_name = bufname(l:goal_bufid)
            if l:info_winid == -1
                " The goals and info windows do not exist.
                " First go to the source's tab and window
                execute l:tabwin[0] . " tabnext"
                execute l:tabwin[1] . " wincmd w"
                " Create the goals window to the right of the source window,
                " and load the goals buffer
                execute "rightbelow vsplit " . fnameescape(bufname(l:goal_name))
            else
                " The goals window does not exist, but the info window does exist.
                " First go to the info's tab and window
                call coquille#WinGoToId(l:info_winid, 0, 0)
                " Create the goals window above the info window, and load the
                " goals buffer
                execute "leftabove split " . fnameescape(bufname(l:goal_name))
            endif
            let l:goal_winid = coquille#WinGetId(tabpagenr(), winnr())
            if coquille#WinVarExists(a:winid, a:hint_tabnr, a:hint_winnr,
                        \            "coquille_shared")
                " The goals window inherits w:coquille_shared from the
                " source window.
                let w:coquille_shared =
                            \ coquille#GetWinVar(a:winid, a:hint_tabnr,
                            \                    a:hint_winnr,
                            \                    "coquille_shared", "")
            endif
            call coquille#SetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                        \           "coquille_goal_winid", l:goal_winid)
        endif
        if l:info_winid == -1
            " See if there is a shared info window in the current tab that
            " can be used. Refresh l:tabwin in case a new tab or window was
            " created above that messed up the numbers.
            let l:tabwin = coquille#WinId2TabWin(a:winid, l:tabwin[0],
                        \                        l:tabwin[1])
            let l:info_winid = coquille#FindSharedWin(l:tabwin[0], "coq-infos")
            if l:info_winid != -1
                call coquille#SetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                            \           "coquille_info_winid", l:info_winid)
            endif
        endif
        if l:info_winid == -1
            " The info window does not exist, but the goals window has to
            " exist because it was created above if necessary.
            let l:info_name = bufname(l:info_bufid)
            " First go to the goals window.
            let l:goal_tabwin = coquille#WinId2TabWin(l:goal_winid, tabpagenr(),
                        \                             winnr())
            execute l:goal_tabwin[0] . " tabnext"
            execute l:goal_tabwin[1] . " wincmd w"
            " Create the info window below the goals window, and load the
            " info buffer.
            execute "rightbelow split " . fnameescape(bufname(l:info_name))
            let l:info_winid = coquille#WinGetId(tabpagenr(), winnr())
            if coquille#WinVarExists(a:winid, a:hint_tabnr, a:hint_winnr,
                        \            "coquille_shared")
                " The info window inherits w:coquille_shared from the
                " source window.
                let w:coquille_shared =
                            \ coquille#GetWinVar(a:winid, a:hint_tabnr,
                            \                    a:hint_winnr,
                            \                    "coquille_shared", "")
            endif
            call coquille#SetWinVar(a:winid, a:hint_tabnr, a:hint_winnr,
                        \           "coquille_info_winid", l:info_winid)
        endif
        " Restore the active window
        call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
        " In case the goal and info windows were not created above, put the
        " correct buffers in them
        call coquille#SetWinBufnr(l:goal_winid, 0, 0, l:goal_bufid)
        call coquille#SetWinBufnr(l:info_winid, 0, 0, l:info_bufid)
    else
        call coquille#GarbageCollectSupportWin(l:goal_winid)
        call coquille#GarbageCollectSupportWin(l:info_winid)
    endif
    call setbufvar(l:bufid, "coquille_block_activate", 0)
endfunction

" Create the pattern string to match from start until stop
"
" start and stop are in the format: [line, col].
"
" The lines and columns are 1-based. The start position is included in the
" match (unless excluded by stop), and the final column in the stop position
" is excluded.
"
" For the stop position, a line of -1 means highlight until the end of the
" file. A column of -1 means highlight until the end of the specified line.
function! coquille#GetRangePattern(start, stop)
    if a:start[0] > a:stop[0]
        let l:pattern = ''
    elseif a:start[0] == a:stop[0]
        " Start the pattern by matching anything on the start line
        let l:pattern = printf('\%%%dl', a:start[0])
        if a:start[1] > 1
            " Refine the pattern to only match columns > (start[1]-1).
            let l:pattern .= printf('\%%>%dc', a:start[1] - 1)
        endif
        if a:stop[1] != -1
            " Refine the pattern to only match columns < (stop[1]).
            let l:pattern .= printf('\%%<%dc', a:stop[1])
        endif
    else
        " Start the pattern by matching anything on the start line
        let l:pattern = printf('\%%%dl', a:start[0])
        if a:start[1] > 1
            " Refine the pattern to only match columns > (start[1]-1) on
            " the first line.
            let l:pattern .= printf('\%%>%dc', a:start[1] - 1)
        endif
        " Create a new branch in the pattern which matches any column of the
        " lines [start[0] + 1, stop[0] - 1] (bounds included).
        "
        " Create one more branch that matches any column in stop[0].
        let l:pattern .= printf('\|\%%>%dl\%%<%dl\|\%%%dl',
                    \           a:start[0],
                    \           a:stop[0],
                    \           a:stop[0])
        if a:stop['col'] != -1
            " Refine that last branch to only match columns < (stop[1]).
            let l:pattern .= printf('\%%<%dc', a:stop[1])
        endif
    endif
    return l:pattern
endfunction

function! coquille#GetRangeListPattern(lst)
    return join(map(copy(a:lst),
            \       'coquille#GetRangePattern(v:val[0], v:val[1])'),
            \   '\|')
endfunction

let s:empty_range = [ { 'line': 0, 'col': 0}, { 'line': 0, 'col': 0} ]

function! coquille#FixWindowScroll(winid, hint_tabnr, hint_winnr)
    let l:cur_tab = tabpagenr()
    let l:cur_win = winnr()
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    if l:tabwin[0] != l:cur_tab
        " Only fix the scroll for windows in the current tab. The other
        " windows will be fixed when their tab is entered.
        return 0
    endif

    if coquille#GetWinVar(a:winid, l:tabwin[0], l:tabwin[1],
                \         "coquille_needs_scroll_fix", 0)
        let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
        call coquille#SetWinVar(a:winid, l:tabwin[0], l:tabwin[1],
                    \           "coquille_needs_scroll_fix", 0)
        " When the cursor is at the last line of the buffer, then the number
        " of lines in the buffer is reduced from python, vim leaves the window
        " scrolled past the end. Just entering the window is enough to fix it.
        call coquille#WinGoToId(a:winid, l:tabwin[0], l:tabwin[1])

        call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
        return 1
    endif
endfunction

function! coquille#FixWindowScrollTabWin(tabnr, winnr)
    let l:winid = coquille#WinGetId(a:tabnr, a:winnr)
    return coquille#FixWindowScroll(l:winid, a:tabnr, a:winnr)
endfunction

function! coquille#SyncWindowColors(winid, hint_tabnr, hint_winnr)
    let l:cur_tab = tabpagenr()
    let l:cur_win = winnr()
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    if l:tabwin[0] != l:cur_tab
        " Only update the colors for windows in the current tab. The colors
        " for windows in other tabs will be updated when their tab is entered.
        return 0
    endif
    let l:bufnr = coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
    " Map from variable name to [group name, priority]
    let l:group_infos = {
    \       "coquille_checked": ["CheckedByCoq", 10],
    \       "coquille_sent": ["SentToCoq", 10],
    \       "coquille_errors": ["CoqError", 11],
    \       "coquille_warnings": ["CoqWarning", 11]
    \   }

    let l:switched = 0
    for group in keys(l:group_infos)
        let l:win_value = coquille#GetWinVar(a:winid, l:tabwin[0], l:tabwin[1],
                    \                        l:group, [s:empty_range, -1])
        let l:buf_value = getbufvar(l:bufnr, l:group, s:empty_range)
        if l:win_value[0] == l:buf_value
            " It already matches; nothing to do
            continue
        endif
        " matchadd() only works for the current window. So switch to a:winid.
        if !l:switched
            let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
            call coquille#WinGoToId(a:winid, l:tabwin[0], l:tabwin[1])
            let l:switched = 1
        endif
        " Switching the window could have triggered the colors to get synced.
        " So double check that they need to be synced.
        let l:win_value = coquille#GetWinVar(a:winid, tabpagenr(), winnr(),
                    \                        l:group, [s:empty_range, -1])
        if l:win_value[0] == l:buf_value
            " It already matches; nothing to do
            continue
        endif
        if l:win_value[1] != -1
            call matchdelete(l:win_value[1])
        endif
        if l:buf_value == s:empty_range
            let l:matchid = -1
        else
            let l:group_info = l:group_infos[l:group]
            let l:matchid = matchadd(l:group_info[0],
                        \            coquille#GetRangeListPattern(l:buf_value),
                        \            l:group_info[1])
        endif
        call coquille#SetWinVar(a:winid, l:tabwin[0], l:tabwin[1],
                    \           l:group, [l:buf_value, l:matchid])
    endfor
    if l:switched
        " Switch back to the original window
        call coquille#WinGoToId(l:cur_winid, l:cur_tab, l:cur_win)
    endif
    return l:switched
endfunction

function! coquille#SyncBufferColors(bufid)
    let l:old_redraw = &lazyredraw
    if !l:old_redraw
        "set lazyredraw
    endif
    let l:need_redraw = 0
    for winid in coquille#WinFindBuf(a:bufid)
        if coquille#SyncWindowColors(l:winid, 0, 0)
            let l:need_redraw = 1
        endif
    endfor
    if !l:old_redraw
        "set nolazyredraw
        if l:need_redraw
            "redraw
        endif
    endif
endfunction

function! coquille#TabActivated()
    " Colors are not synced for windows in inactive tabs, so when a tab
    " becomes active, all of its colors need to be synced
    let l:search_win = 1
    let l:cur_tab = tabpagenr()
    let l:cur_win = 1
    while l:cur_win <= tabpagewinnr(l:cur_tab, "$")
        let l:cur_winid = coquille#WinGetId(l:cur_tab, l:cur_win)
        call coquille#SyncWindowColors(l:cur_winid, l:cur_tab, l:cur_win)
        call coquille#FixWindowScroll(l:cur_winid, l:cur_tab, l:cur_win)
        let l:cur_win += 1
    endwhile

endfunction

function! coquille#WindowActivated(winid, hint_tabnr, hint_winnr)
    " If there was a previously active coquille window, and it was closed,
    " then its support windows need to be closed too
    if s:active_winid != -1
        if !coquille#WinIdExists(s:active_winid, s:active_tabnr, s:active_winnr)
            " The source window was closed, so close the support windows
            call coquille#GarbageCollectSupportWin(s:active_goal_winid)
            call coquille#GarbageCollectSupportWin(s:active_info_winid)
        endif
    endif
    let l:tabwin = coquille#WinId2TabWin(a:winid, a:hint_tabnr, a:hint_winnr)
    let l:goal_winid = coquille#GetWinVar(a:winid, l:tabwin[0],
                \                         l:tabwin[1],
                \                         "coquille_goal_winid", -1)
    let l:info_winid = coquille#GetWinVar(a:winid, l:tabwin[0],
                \                         l:tabwin[1],
                \                         "coquille_info_winid", -1)
    if l:goal_winid != -1 && l:info_winid != -1
        let s:active_winid = a:winid
        let s:active_tabnr = l:tabwin[0]
        let s:active_winnr = l:tabwin[1]
        let s:active_goal_winid = l:goal_winid
        let s:active_info_winid = l:info_winid
    endif
    call coquille#UpdateSupportingWindows(a:winid, l:tabwin[0], l:tabwin[1])
    call coquille#SyncWindowColors(a:winid, l:tabwin[0], l:tabwin[1])
endfunction

function! coquille#EnsureLaunched(winid, ...)
    " The colors are set as late as possible to give the background option
    " more time to get set.
    if &background == 'dark'
        hi default CheckedByCoq ctermbg=22 guibg=DarkGreen
        hi default SentToCoq ctermbg=65 guibg=DarkOliveGreen
        hi default CoqWarning ctermbg=94 guibg=goldenrod4
    else
        hi default CheckedByCoq ctermbg=22 guibg=DarkGreen
        hi default CheckedByCoq ctermbg=120 guibg=LightGreen
        hi default SentToCoq ctermbg=77 guibg=LimeGreen
        hi default CoqWarning ctermbg=220 guibg=gold
    endif
    hi link CoqError Error

    let l:tabwin = coquille#WinId2TabWin(a:winid, tabpagenr(), winnr())
    let l:bufid = coquille#TabWinBufnr(l:tabwin[0], l:tabwin[1])
    if ! call(function("coquille#OpenSupportingBuffers"),
                \ extend([l:bufid], a:000))
        return -1
    endif
    call coquille#WindowActivated(a:winid, l:tabwin[0], l:tabwin[1])
    return l:bufid
endfunction

function! coquille#Launch(...)
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#TabWinBufnr(tabpagenr(), winnr())
    let l:goal_bufid = getbufvar(l:bufid, "coquille_goal_bufid", -1)
    let l:info_bufid = getbufvar(l:bufid, "coquille_info_bufid", -1)
    if l:goal_bufid != -1 && l:info_bufid != -1
        echo "Coq is already running"
    else
        call call(function("coquille#EnsureLaunched"),
                \ extend([l:winid], a:000))
    endif
endfunction

function! coquille#GotoLastSentDot()
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#EnsureLaunched(l:winid)
    call coquille#Python('coquille.BufferState.lookup_bufid(' . l:bufid . ').' .
                \ '.goto_last_sent_dot()')
endfunction

function! coquille#CoqNext()
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#EnsureLaunched(l:winid)
    call coquille#Python('coquille.BufferState.lookup_bufid(' . l:bufid . ').' .
                \ 'coq_next()')
endfunction

function! coquille#CoqRewind()
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#EnsureLaunched(l:winid)
    call coquille#Python('coquille.BufferState.lookup_bufid(' . l:bufid . ').' .
                \ 'coq_rewind()')
endfunction

function! coquille#CoqToCursor()
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#EnsureLaunched(l:winid)
    call coquille#Python('coquille.BufferState.lookup_bufid(' . l:bufid . ').' .
                \ 'coq_to_cursor()')
endfunction

function! coquille#RawQuery(...)
    let l:winid = coquille#WinGetId(tabpagenr(), winnr())
    let l:bufid = coquille#EnsureLaunched(l:winid)
    call coquille#Python('coquille.BufferState.lookup_bufid(' . l:bufid . ').' .
                \ 'coq_raw_query(*'.  string(a:000) . ')')
endfunction

function! coquille#Register()
    let b:checked = -1
    let b:sent    = -1
    let b:errors  = -1

    " make the different commands accessible
    command! -buffer GotoDot call coquille#GotoLastSentDot()
    command! -buffer CoqNext call coquille#CoqNext()
    command! -buffer CoqUndo call coquille#CoqRewind()
    command! -buffer CoqToCursor call coquille#CoqToCursor()
    command! -buffer CoqKill call coquille#KillSession()

    command! -buffer -nargs=* Coq call coquille#RawQuery(<f-args>)

    command! -bar -buffer -nargs=* -complete=file CoqLaunch call coquille#Launch(<f-args>)

    augroup coquille
        autocmd!
        autocmd WinEnter * call coquille#WindowActivated(
                    \ coquille#WinGetId(tabpagenr(), winnr()),
                    \ tabpagenr(), winnr())
        autocmd TabEnter * call coquille#TabActivated()
        autocmd BufWinEnter * call coquille#WindowActivated(
                    \ coquille#WinGetId(tabpagenr(), winnr()),
                    \ tabpagenr(), winnr())
    augroup END
endfunction
