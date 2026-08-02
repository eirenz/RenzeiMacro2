; ============================================================================
; recorder.ahk — Input Recording Engine
; ============================================================================
; Records mouse and keyboard events as normalized coordinates relative to
; the Container. Saves recordings as JSON files.
; ============================================================================

#Requires AutoHotkey v2.0

class Recorder {
    ; Recording state
    isRecording := false
    events := []
    startTime := 0
    
    ; Reference to the Container instance
    container := ""
    
    ; Mouse-mode reference for tagging 3D events
    mouseMode := ""
    
    ; Mouse tracking
    lastMouseX := 0
    lastMouseY := 0
    mouseTrackTimer := 0
    static MouseTrackInterval := 16  ; ~60fps tracking
    static MouseMoveThreshold := 2   ; Min pixel movement to record
    
    ; Hook handles
    mouseHook := 0
    keyboardHook := 0
    
    ; -----------------------------------------------------------------------
    ; __New(container, mouseMode)
    ; -----------------------------------------------------------------------
    __New(container, mouseMode := "") {
        this.container := container
        this.mouseMode := mouseMode
    }
    
    ; -----------------------------------------------------------------------
    ; StartRecording() — Begin capturing input events
    ; -----------------------------------------------------------------------
    StartRecording() {
        if (this.isRecording)
            return
        
        ; Ensure container is valid
        if (!this.container.isValid) {
            this.container.AutoDetect()
            if (!this.container.isValid)
                throw Error("Cannot start recording — Container not detected")
        }
        
        this.events := []
        this.startTime := this._GetTimestamp()
        this.isRecording := true
        
        ; Start mouse position tracking timer
        this._StartMouseTracking()
        
        ; Install input hooks
        this._InstallHooks()
    }
    
    ; -----------------------------------------------------------------------
    ; StopRecording() — Stop capturing and return the events array
    ; Returns: the events array
    ; -----------------------------------------------------------------------
    StopRecording() {
        if (!this.isRecording)
            return []
        
        this.isRecording := false
        this._StopMouseTracking()
        this._RemoveHooks()
        
        return this.events
    }
    
    ; -----------------------------------------------------------------------
    ; SaveRecording(filePath) — Save events to a JSON file
    ; -----------------------------------------------------------------------
    SaveRecording(filePath) {
        recording := Map(
            "version", 1,
            "created", FormatTime(, "yyyy-MM-ddTHH:mm:ss"),
            "container", Map("width", this.container.w, "height", this.container.h),
            "event_count", this.events.Length,
            "events", this.events
        )
        
        jsonStr := this._SerializeRecording(recording)
        
        ; Write to file
        f := FileOpen(filePath, "w", "UTF-8")
        f.Write(jsonStr)
        f.Close()
        
        return filePath
    }
    
    ; -----------------------------------------------------------------------
    ; LoadRecording(filePath) — Load events from a JSON file
    ; Returns: Map with recording data
    ; -----------------------------------------------------------------------
    static LoadRecording(filePath) {
        if (!FileExist(filePath))
            throw Error("Recording file not found: " filePath)
        
        content := FileRead(filePath, "UTF-8")
        ; Use the IPC client's JSON parser (shared utility)
        ; For now, return raw content — the Player will parse it
        return content
    }
    
    ; === Private Methods ===================================================
    
    _GetTimestamp() {
        return DllCall("QueryPerformanceCounter", "Int64*", &count := 0) ? count : A_TickCount
    }
    
    _GetElapsedMs() {
        static freq := 0
        if (!freq)
            DllCall("QueryPerformanceFrequency", "Int64*", &freq)
        
        if (freq) {
            current := 0
            DllCall("QueryPerformanceCounter", "Int64*", &current)
            return Round((current - this.startTime) / freq * 1000)
        }
        return A_TickCount - this.startTime
    }
    
    ; --- Mouse tracking ---
    
    _StartMouseTracking() {
        ; Use a periodic callback to track mouse position
        timerFn := ObjBindMethod(this, "_OnMouseTrack")
        SetTimer(timerFn, Recorder.MouseTrackInterval)
        this.mouseTrackTimer := timerFn
    }
    
    _StopMouseTracking() {
        if (this.mouseTrackTimer) {
            SetTimer(this.mouseTrackTimer, 0)
            this.mouseTrackTimer := 0
        }
    }
    
    _OnMouseTrack() {
        if (!this.isRecording)
            return
        
        MouseGetPos(&mx, &my)
        
        ; Only record if the mouse moved significantly
        dx := Abs(mx - this.lastMouseX)
        dy := Abs(my - this.lastMouseY)
        if (dx < Recorder.MouseMoveThreshold && dy < Recorder.MouseMoveThreshold)
            return
        
        ; Only record if mouse is within the container
        if (!this.container.IsPointInContainer(mx, my))
            return
        
        this.lastMouseX := mx
        this.lastMouseY := my
        
        ; Check if we're in 3D mode
        is3D := (this.mouseMode && this.mouseMode.currentState = "MODE_3D")
        
        if (is3D) {
            ; In 3D mode, store relative deltas instead of normalized coords
            this._AddEvent(Map(
                "t", this._GetElapsedMs(),
                "type", "mouseMove",
                "mouseMode", "3d",
                "dx", dx * (mx > this.lastMouseX ? 1 : -1),
                "dy", dy * (my > this.lastMouseY ? 1 : -1)
            ))
        } else {
            ; Normal 2D mode — normalized coordinates
            coords := this.container.NormalizeCoords(mx, my)
            this._AddEvent(Map(
                "t", this._GetElapsedMs(),
                "type", "mouseMove",
                "nx", Round(coords.nx, 6),
                "ny", Round(coords.ny, 6)
            ))
        }
    }
    
    ; --- Input hooks ---
    
    _InstallHooks() {
        ; Use InputHook for keyboard events
        this.keyboardHook := InputHook("L0 I1")  ; Length 0 (don't consume), Input level 1
        this.keyboardHook.KeyOpt("{All}", "N")    ; Notify on all keys
        this.keyboardHook.OnKeyDown := ObjBindMethod(this, "_OnKeyDown")
        this.keyboardHook.OnKeyUp := ObjBindMethod(this, "_OnKeyUp")
        this.keyboardHook.Start()
        
        ; For mouse buttons, use hotkey-based detection
        this._InstallMouseHooks()
    }
    
    _RemoveHooks() {
        if (this.keyboardHook) {
            this.keyboardHook.Stop()
            this.keyboardHook := 0
        }
        this._RemoveMouseHooks()
    }
    
    _InstallMouseHooks() {
        ; Register mouse button hotkeys (passthrough with ~)
        HotIf((*) => this.isRecording)
        Hotkey("~LButton", ObjBindMethod(this, "_OnMouseButton", "left", "down"))
        Hotkey("~LButton Up", ObjBindMethod(this, "_OnMouseButton", "left", "up"))
        Hotkey("~RButton", ObjBindMethod(this, "_OnMouseButton", "right", "down"))
        Hotkey("~RButton Up", ObjBindMethod(this, "_OnMouseButton", "right", "up"))
        Hotkey("~MButton", ObjBindMethod(this, "_OnMouseButton", "middle", "down"))
        Hotkey("~MButton Up", ObjBindMethod(this, "_OnMouseButton", "middle", "up"))
        HotIf()
    }
    
    _RemoveMouseHooks() {
        try {
            HotIf((*) => this.isRecording)
            Hotkey("~LButton", "Off")
            Hotkey("~LButton Up", "Off")
            Hotkey("~RButton", "Off")
            Hotkey("~RButton Up", "Off")
            Hotkey("~MButton", "Off")
            Hotkey("~MButton Up", "Off")
            HotIf()
        }
    }
    
    _OnMouseButton(button, action, *) {
        if (!this.isRecording)
            return
        
        MouseGetPos(&mx, &my)
        
        if (!this.container.IsPointInContainer(mx, my))
            return
        
        coords := this.container.NormalizeCoords(mx, my)
        eventType := (action = "down") ? "mouseDown" : "mouseUp"
        
        this._AddEvent(Map(
            "t", this._GetElapsedMs(),
            "type", eventType,
            "button", button,
            "nx", Round(coords.nx, 6),
            "ny", Round(coords.ny, 6)
        ))
    }
    
    _OnKeyDown(ih, vk, sc) {
        if (!this.isRecording)
            return
        
        keyName := GetKeyName(Format("vk{:x}sc{:x}", vk, sc))
        if (!keyName)
            return
        
        this._AddEvent(Map(
            "t", this._GetElapsedMs(),
            "type", "keyDown",
            "key", keyName
        ))
    }
    
    _OnKeyUp(ih, vk, sc) {
        if (!this.isRecording)
            return
        
        keyName := GetKeyName(Format("vk{:x}sc{:x}", vk, sc))
        if (!keyName)
            return
        
        this._AddEvent(Map(
            "t", this._GetElapsedMs(),
            "type", "keyUp",
            "key", keyName
        ))
    }
    
    _AddEvent(eventMap) {
        this.events.Push(eventMap)
    }
    
    ; --- JSON Serialization for recordings ---
    
    _SerializeRecording(recording) {
        out := "{"
        out .= '"version":' recording["version"] ","
        out .= '"created":"' recording["created"] '",'
        out .= '"container":{"width":' recording["container"]["width"] ',"height":' recording["container"]["height"] '},'
        out .= '"event_count":' recording["event_count"] ","
        out .= '"events":['
        
        events := recording["events"]
        for i, evt in events {
            if (i > 1)
                out .= ","
            out .= this._SerializeEvent(evt)
        }
        
        out .= "]}"
        return out
    }
    
    _SerializeEvent(evt) {
        parts := []
        for key, val in evt {
            if (val is Number)
                parts.Push('"' key '":' val)
            else
                parts.Push('"' key '":"' val '"')
        }
        
        joined := ""
        for i, part in parts {
            if (i > 1)
                joined .= ","
            joined .= part
        }
        return "{" joined "}"
    }
}
