#!/usr/bin/env bash
# Windows 侧调用 rpc.py 的稳定封装：rpcw.sh <rpc参数...>
D=/mnt/d/Users/NoWorld/Documents/source/Neow/neow-bridge
cmd.exe /c "C:\\Users\\NoWorld\\AppData\\Local\\Python\\bin\\python.exe D:\\Users\\NoWorld\\Documents\\source\\Neow\\neow-bridge\\scripts\\rpc.py $*" 2>&1 | tr -d '\r'
