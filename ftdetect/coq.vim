" Set the file type to coq for coq source files. For the supporting buffers,
" restore the file type after it gets overridden by other file type detectors.
au BufRead,BufNewFile *.v let &filetype = getbufvar("", "coquille_filetype", "coq")
