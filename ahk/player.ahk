; ============================================================================
; player.ahk — Macro Playback Engine
; ============================================================================
; Replays recorded events from JSON, denormalizing coordinates to the
; current Container position/size. Supports looping, 2D/3D mouse modes.
; ============================================================================

#Requires AutoHotkey v2.0

class Player {
    ; Playback state
    isPlaying := false
    isPaused := false
    shouldLoop := true
    shouldStop := false
    
    ; References
    container := ""
    mouseMode := ""
    
    ; Current recording data
    events := []
    currentIndex := 0
    
    ; Timing
    playbackStartTime := 0
    
    ; -----------------------------------------------------------------------
    ; __New(container, mouseMode)
    ; -----------------------------------------------------------------------
    __New(container, mouseMode := "") {
        this.container := container
        this.mouseMode := mouseMode
    }
    
    ; -----------------------------------------------------------------------
    ; LoadRecording(jsonStr) — Parse a JSON recording string into events
    ; -----------------------------------------------------------------------
    LoadRecording(jsonStr) {
        this.events := this._ParseRecording(jsonStr)
        this.currentIndex := 0
        return this.events.Length
    }
    
    ; -----------------------------------------------------------------------
    ; LoadFromFile(filePath) — Load and parse a recording from a file
    ; -----------------------------------------------------------------------
    LoadFromFile(filePath) {
        if (!FileExist(filePath))
            throw Error("Recording file not found: " filePath)
        
        content := FileRead(filePath, "UTF-8")
        return this.LoadRecording(content)
    }
    
    ; -----------------------------------------------------------------------
    ; Play(loop) — Start playback. Blocks until complete or stopped.
    ; -----------------------------------------------------------------------
    Play(loop := true) {
        if (this.events.Length = 0)
            throw Error("No recording loaded")
        
        ; Refresh container before playback
        if (!this.container.Refresh())
            throw Error("Cannot play — Container not detected")
        
        this.isPlaying := true
        this.shouldStop := false
        this.shouldLoop := loop
        
        while (!this.shouldStop) {
            this._PlayOnce()
            
            if (!this.shouldLoop || this.shouldStop)
                break
            
            ; Small pause between loops
            Sleep(100)
            
            ; Re-detect container between loops (it may have moved)
            this.container.Refresh()
        }
        
        this.isPlaying := false
    }
    
    ; -----------------------------------------------------------------------
    ; PlayOnce() — Play the recording exactly once (non-looping)
    ; -----------------------------------------------------------------------
    PlayOnce() {
        this.Play(false)
    }
    
    ; -----------------------------------------------------------------------
    ; Stop() — Signal playback to stop
    ; -----------------------------------------------------------------------
    Stop() {
        this.shouldStop := true
    }
    
    ; -----------------------------------------------------------------------
    ; Pause() / Resume()
    ; -----------------------------------------------------------------------
    Pause() {
        this.isPaused := true
    }
    
    Resume() {
        this.isPaused := false
    }
    
    ; === Private Methods ===================================================
    
    _PlayOnce() {
        this.currentIndex := 1
        this.playbackStartTime := A_TickCount
        
        while (this.currentIndex <= this.events.Length && !this.shouldStop) {
            ; Handle pause
            while (this.isPaused && !this.shouldStop)
                Sleep(50)
            
            if (this.shouldStop)
                break
            
            evt := this.events[this.currentIndex]
            
            ; Wait until it's time to execute this event
            targetTime := evt.Has("t") ? evt["t"] : 0
            this._WaitUntil(targetTime)
            
            if (this.shouldStop)
                break
            
            ; Execute the event
            this._ExecuteEvent(evt)
            
            this.currentIndex++
        }
    }
    
    _WaitUntil(targetMs) {
        while (!this.shouldStop) {
            elapsed := A_TickCount - this.playbackStartTime
            if (elapsed >= targetMs)
                return
            
            remaining := targetMs - elapsed
            if (remaining > 15)
                Sleep(1)  ; Yield but stay responsive
            else
                DllCall("Sleep", "UInt", 0)  ; Tight spin for precision
        }
    }
    
    _ExecuteEvent(evt) {
        if (!evt.Has("type"))
            return
        
        type := evt["type"]
        
        switch type {
            case "mouseMove":
                this._ExecMouseMove(evt)
            case "mouseDown":
                this._ExecMouseDown(evt)
            case "mouseUp":
                this._ExecMouseUp(evt)
            case "keyDown":
                this._ExecKeyDown(evt)
            case "keyUp":
                this._ExecKeyUp(evt)
        }
    }
    
    _ExecMouseMove(evt) {
        ; Check if this is a 3D-mode move (relative deltas)
        if (evt.Has("mouseMode") && evt["mouseMode"] = "3d") {
            dx := evt.Has("dx") ? evt["dx"] : 0
            dy := evt.Has("dy") ? evt["dy"] : 0
            ; Relative mouse move
            DllCall("mouse_event", "UInt", 0x0001, "Int", dx, "Int", dy, "UInt", 0, "UPtr", 0)
            return
        }
        
        ; Normal 2D mode — denormalize and move
        if (!evt.Has("nx") || !evt.Has("ny"))
            return
        
        coords := this.container.DenormalizeCoords(evt["nx"], evt["ny"])
        
        ; Use SendInput-style absolute mouse move for precision
        ; Convert to 0-65535 range for absolute positioning
        screenW := SysGet(78)  ; SM_CXSCREEN
        screenH := SysGet(79)  ; SM_CYSCREEN
        absX := Round(coords.absX / screenW * 65535)
        absY := Round(coords.absY / screenH * 65535)
        
        DllCall("mouse_event"
            , "UInt", 0x0001 | 0x8000  ; MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
            , "Int", absX
            , "Int", absY
            , "UInt", 0
            , "UPtr", 0)
    }
    
    _ExecMouseDown(evt) {
        if (!evt.Has("nx") || !evt.Has("ny") || !evt.Has("button"))
            return
        
        coords := this.container.DenormalizeCoords(evt["nx"], evt["ny"])
        
        ; Move to position first
        MouseMove(coords.absX, coords.absY, 0)
        Sleep(1)
        
        ; Press button
        button := evt["button"]
        switch button {
            case "left":
                DllCall("mouse_event", "UInt", 0x0002, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_LEFTDOWN
            case "right":
                DllCall("mouse_event", "UInt", 0x0008, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_RIGHTDOWN
            case "middle":
                DllCall("mouse_event", "UInt", 0x0020, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_MIDDLEDOWN
        }
    }
    
    _ExecMouseUp(evt) {
        if (!evt.Has("button"))
            return
        
        ; If coords provided, move there first
        if (evt.Has("nx") && evt.Has("ny")) {
            coords := this.container.DenormalizeCoords(evt["nx"], evt["ny"])
            MouseMove(coords.absX, coords.absY, 0)
            Sleep(1)
        }
        
        button := evt["button"]
        switch button {
            case "left":
                DllCall("mouse_event", "UInt", 0x0004, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_LEFTUP
            case "right":
                DllCall("mouse_event", "UInt", 0x0010, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_RIGHTUP
            case "middle":
                DllCall("mouse_event", "UInt", 0x0040, "Int", 0, "Int", 0, "UInt", 0, "UPtr", 0)  ; MOUSEEVENTF_MIDDLEUP
        }
    }
    
    _ExecKeyDown(evt) {
        if (!evt.Has("key"))
            return
        key := evt["key"]
        SendInput("{" key " down}")
    }
    
    _ExecKeyUp(evt) {
        if (!evt.Has("key"))
            return
        key := evt["key"]
        SendInput("{" key " up}")
    }
    
    ; --- Minimal JSON parsing for recording files ---
    ; This parses the recording JSON format specifically.
    
    _ParseRecording(jsonStr) {
        events := []
        
        ; Find the "events" array
        eventsStart := InStr(jsonStr, '"events"')
        if (!eventsStart)
            return events
        
        ; Find the opening bracket
        bracketStart := InStr(jsonStr, "[", , eventsStart)
        if (!bracketStart)
            return events
        
        ; Find each event object { ... }
        pos := bracketStart + 1
        len := StrLen(jsonStr)
        
        while (pos < len) {
            ; Find next opening brace
            objStart := InStr(jsonStr, "{", , pos)
            if (!objStart)
                break
            
            ; Find the matching closing brace (handle nesting)
            braceCount := 1
            objEnd := objStart + 1
            while (objEnd <= len && braceCount > 0) {
                ch := SubStr(jsonStr, objEnd, 1)
                if (ch = "{")
                    braceCount++
                else if (ch = "}")
                    braceCount--
                if (braceCount > 0)
                    objEnd++
            }
            
            ; Parse this event object
            objStr := SubStr(jsonStr, objStart, objEnd - objStart + 1)
            evt := this._ParseEventObj(objStr)
            if (evt.Count > 0)
                events.Push(evt)
            
            pos := objEnd + 1
            
            ; Check if we've hit the closing bracket of the events array
            nextBracket := InStr(jsonStr, "]", , pos)
            nextBrace := InStr(jsonStr, "{", , pos)
            if (nextBracket && (!nextBrace || nextBracket < nextBrace))
                break
        }
        
        return events
    }
    
    _ParseEventObj(objStr) {
        result := Map()
        ; Strip outer braces
        inner := SubStr(objStr, 2, StrLen(objStr) - 2)
        
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
            
            ; Parse key
            if (SubStr(inner, pos, 1) != '"')
                break
            keyEnd := InStr(inner, '"', , pos + 1)
            if (!keyEnd)
                break
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
                ; String
                valEnd := InStr(inner, '"', , pos + 1)
                if (!valEnd)
                    break
                result[key] := SubStr(inner, pos + 1, valEnd - pos - 1)
                pos := valEnd + 1
            } else {
                ; Number or other
                numStart := pos
                while (pos <= len) {
                    c := SubStr(inner, pos, 1)
                    if (c >= "0" && c <= "9" || c = "." || c = "-" || c = "e" || c = "E" || c = "+")
                        pos++
                    else
                        break
                }
                numStr := SubStr(inner, numStart, pos - numStart)
                if (numStr != "")
                    result[key] := Number(numStr)
            }
        }
        
        return result
    }
}
