; ============================================================================
; ipc_client.ahk — Named Pipe Client (AHK → Python)
; ============================================================================
; Communicates with the Python vision service over a Windows named pipe.
; Sends JSON queries, receives JSON responses.
; ============================================================================

#Requires AutoHotkey v2.0

class IPCClient {
    static PipeName := "\\.\pipe\RenzeiMacroVision"
    
    pipeHandle := 0
    isConnected := false
    
    ; Buffer size for reads
    static BufferSize := 65536
    
    ; -----------------------------------------------------------------------
    ; Connect() — Open the named pipe to the Python vision service
    ; Returns: true if connection established
    ; -----------------------------------------------------------------------
    Connect() {
        if (this.isConnected)
            return true
        
        ; Try to open the named pipe (Python server must already be listening)
        GENERIC_READ := 0x80000000
        GENERIC_WRITE := 0x40000000
        OPEN_EXISTING := 3
        FILE_FLAG_OVERLAPPED := 0  ; Using synchronous I/O for simplicity
        
        this.pipeHandle := DllCall("CreateFile"
            , "Str", IPCClient.PipeName
            , "UInt", GENERIC_READ | GENERIC_WRITE
            , "UInt", 0              ; No sharing
            , "Ptr", 0               ; Default security
            , "UInt", OPEN_EXISTING
            , "UInt", 0              ; Normal attributes
            , "Ptr", 0               ; No template
            , "Ptr")
        
        INVALID_HANDLE_VALUE := -1
        if (this.pipeHandle = INVALID_HANDLE_VALUE || this.pipeHandle = 0) {
            this.pipeHandle := 0
            this.isConnected := false
            return false
        }
        
        ; Set pipe to message-read mode
        PIPE_READMODE_MESSAGE := 0x00000002
        mode := PIPE_READMODE_MESSAGE
        DllCall("SetNamedPipeHandleState"
            , "Ptr", this.pipeHandle
            , "UInt*", &mode
            , "Ptr", 0
            , "Ptr", 0)
        
        this.isConnected := true
        return true
    }
    
    ; -----------------------------------------------------------------------
    ; Disconnect() — Close the pipe
    ; -----------------------------------------------------------------------
    Disconnect() {
        if (this.pipeHandle) {
            DllCall("CloseHandle", "Ptr", this.pipeHandle)
            this.pipeHandle := 0
        }
        this.isConnected := false
    }
    
    ; -----------------------------------------------------------------------
    ; SendQuery(queryMap) — Send a JSON query and get a JSON response back
    ; queryMap: a Map object that will be serialized to JSON
    ; Returns: a Map object parsed from the JSON response, or empty Map on error
    ; -----------------------------------------------------------------------
    SendQuery(queryMap) {
        if (!this.isConnected) {
            if (!this.Connect())
                return Map("error", "Not connected to vision service")
        }
        
        ; Serialize the query map to JSON
        jsonStr := this._MapToJson(queryMap)
        
        ; Write to pipe
        bytesWritten := 0
        writeBuffer := Buffer(StrPut(jsonStr, "UTF-8"), 0)
        StrPut(jsonStr, writeBuffer, "UTF-8")
        writeLen := StrLen(jsonStr)  ; byte length for ASCII/UTF-8 simple JSON
        
        result := DllCall("WriteFile"
            , "Ptr", this.pipeHandle
            , "Ptr", writeBuffer
            , "UInt", writeBuffer.Size - 1  ; Exclude null terminator
            , "UInt*", &bytesWritten
            , "Ptr", 0)
        
        if (!result) {
            this.isConnected := false
            return Map("error", "Write failed")
        }
        
        ; Read response
        readBuffer := Buffer(IPCClient.BufferSize, 0)
        bytesRead := 0
        
        result := DllCall("ReadFile"
            , "Ptr", this.pipeHandle
            , "Ptr", readBuffer
            , "UInt", IPCClient.BufferSize
            , "UInt*", &bytesRead
            , "Ptr", 0)
        
        if (!result || bytesRead = 0) {
            this.isConnected := false
            return Map("error", "Read failed")
        }
        
        responseStr := StrGet(readBuffer, bytesRead, "UTF-8")
        return this._JsonToMap(responseStr)
    }
    
    ; -----------------------------------------------------------------------
    ; QueryTemplateMatch(templateName, nx1, ny1, nx2, ny2)
    ; Ask Python: "is this template visible in this region?"
    ; -----------------------------------------------------------------------
    QueryTemplateMatch(templateName, nx1 := 0.0, ny1 := 0.0, nx2 := 1.0, ny2 := 1.0) {
        query := Map(
            "cmd", "template_match",
            "template", templateName,
            "region", Map("nx1", nx1, "ny1", ny1, "nx2", nx2, "ny2", ny2)
        )
        return this.SendQuery(query)
    }
    
    ; -----------------------------------------------------------------------
    ; QueryOCR(nx1, ny1, nx2, ny2)
    ; Ask Python: "read text in this region"
    ; -----------------------------------------------------------------------
    QueryOCR(nx1, ny1, nx2, ny2) {
        query := Map(
            "cmd", "ocr_read",
            "region", Map("nx1", nx1, "ny1", ny1, "nx2", nx2, "ny2", ny2)
        )
        return this.SendQuery(query)
    }
    
    ; -----------------------------------------------------------------------
    ; QueryContainer()
    ; Ask Python for the current container rect
    ; -----------------------------------------------------------------------
    QueryContainer() {
        return this.SendQuery(Map("cmd", "get_container"))
    }
    
    ; -----------------------------------------------------------------------
    ; QueryIsDisconnected()
    ; Ask Python: "is the disconnect dialog visible?"
    ; -----------------------------------------------------------------------
    QueryIsDisconnected() {
        return this.SendQuery(Map("cmd", "is_disconnected"))
    }

    ; -----------------------------------------------------------------------
    ; PlayMacro(filePath, loop)
    ; Ask Python: "play this macro sequence"
    ; -----------------------------------------------------------------------
    PlayMacro(filePath, loop := true) {
        query := Map(
            "cmd", "play_macro",
            "file", filePath,
            "loop", loop
        )
        return this.SendQuery(query)
    }

    ; -----------------------------------------------------------------------
    ; StopMacro()
    ; Ask Python: "stop the macro playback"
    ; -----------------------------------------------------------------------
    StopMacro() {
        return this.SendQuery(Map("cmd", "stop_macro"))
    }
    
    ; -----------------------------------------------------------------------
    ; QueryGameLoaded()
    ; Ask Python: "is the game visibly loaded?"
    ; -----------------------------------------------------------------------
    QueryGameLoaded() {
        return this.SendQuery(Map("cmd", "is_game_loaded"))
    }
    
    ; -----------------------------------------------------------------------
    ; NotifyDisconnect()
    ; Tell Python that AHK has detected a need to reconnect
    ; -----------------------------------------------------------------------
    NotifyReconnect() {
        return this.SendQuery(Map("cmd", "start_reconnect"))
    }
    
    ; === Simple JSON Serialization (AHK Map ↔ JSON string) ================
    ; These are minimal implementations for the structured IPC messages.
    ; They handle flat and one-level-nested Maps with string/number values.
    
    _MapToJson(obj) {
        if (obj is Map)
            return this._SerializeMap(obj)
        if (IsNumber(obj))
            return String(obj)
        if (obj is String)
            return '"' this._EscapeJson(obj) '"'
        return '""'
    }
    
    _SerializeMap(m) {
        parts := []
        for key, val in m {
            keyStr := '"' this._EscapeJson(String(key)) '"'
            valStr := this._MapToJson(val)
            parts.Push(keyStr ":" valStr)
        }
        joined := ""
        for i, part in parts {
            if (i > 1)
                joined .= ","
            joined .= part
        }
        return "{" joined "}"
    }
    
    _EscapeJson(s) {
        s := StrReplace(s, "\", "\\")
        s := StrReplace(s, '"', '\"')
        s := StrReplace(s, "`n", "\n")
        s := StrReplace(s, "`r", "\r")
        s := StrReplace(s, "`t", "\t")
        return s
    }
    
    ; Minimal JSON parser — handles objects with string/number/bool/nested-object values
    _JsonToMap(jsonStr) {
        jsonStr := Trim(jsonStr)
        if (SubStr(jsonStr, 1, 1) != "{")
            return Map("raw", jsonStr)
        
        result := Map()
        ; Strip outer braces
        inner := SubStr(jsonStr, 2, StrLen(jsonStr) - 2)
        
        ; Simple state-based parsing
        pos := 1
        len := StrLen(inner)
        
        while (pos <= len) {
            ; Skip whitespace and commas
            while (pos <= len) {
                ch := SubStr(inner, pos, 1)
                if (ch = " " || ch = "`t" || ch = "`n" || ch = "`r" || ch = ",")
                    pos++
                else
                    break
            }
            if (pos > len)
                break
            
            ; Parse key (must be a quoted string)
            if (SubStr(inner, pos, 1) != '"')
                break
            keyEnd := this._FindClosingQuote(inner, pos + 1)
            key := SubStr(inner, pos + 1, keyEnd - pos - 1)
            pos := keyEnd + 1
            
            ; Skip colon and whitespace
            while (pos <= len) {
                ch := SubStr(inner, pos, 1)
                if (ch = ":" || ch = " " || ch = "`t")
                    pos++
                else
                    break
            }
            
            ; Parse value
            ch := SubStr(inner, pos, 1)
            if (ch = '"') {
                ; String value
                valEnd := this._FindClosingQuote(inner, pos + 1)
                val := SubStr(inner, pos + 1, valEnd - pos - 1)
                val := StrReplace(val, "\\", "\")
                val := StrReplace(val, '\"', '"')
                pos := valEnd + 1
                result[key] := val
            } else if (ch = "{") {
                ; Nested object — find matching brace
                braceCount := 1
                start := pos
                pos++
                while (pos <= len && braceCount > 0) {
                    c := SubStr(inner, pos, 1)
                    if (c = "{")
                        braceCount++
                    else if (c = "}")
                        braceCount--
                    pos++
                }
                nestedJson := SubStr(inner, start, pos - start)
                result[key] := this._JsonToMap(nestedJson)
            } else if (SubStr(inner, pos, 4) = "true") {
                result[key] := true
                pos += 4
            } else if (SubStr(inner, pos, 5) = "false") {
                result[key] := false
                pos += 5
            } else if (SubStr(inner, pos, 4) = "null") {
                result[key] := ""
                pos += 4
            } else {
                ; Number — read until non-numeric
                numStart := pos
                while (pos <= len) {
                    c := SubStr(inner, pos, 1)
                    if (c >= "0" && c <= "9" || c = "." || c = "-" || c = "e" || c = "E" || c = "+")
                        pos++
                    else
                        break
                }
                numStr := SubStr(inner, numStart, pos - numStart)
                result[key] := Number(numStr)
            }
        }
        
        return result
    }
    
    _FindClosingQuote(s, startPos) {
        pos := startPos
        len := StrLen(s)
        while (pos <= len) {
            ch := SubStr(s, pos, 1)
            if (ch = '"')
                return pos
            if (ch = "\")
                pos += 2  ; Skip escaped char
            else
                pos++
        }
        return len + 1
    }
}
