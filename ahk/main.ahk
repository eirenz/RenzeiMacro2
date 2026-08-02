; ============================================================================
; main.ahk — Entry Point, Global Hotkeys, Orchestration
; ============================================================================
; Launches the Python vision service, establishes IPC, registers global
; hotkeys, and orchestrates recording/playback/reconnect.
; ============================================================================

#Requires AutoHotkey v2.0
#SingleInstance Force

; Include all modules
#Include "container.ahk"
#Include "ipc_client.ahk"
#Include "recorder.ahk"
#Include "mouse_mode.ahk"

; ============================================================================
; Global State
; ============================================================================

global AppState := Map(
    "mode", "idle",           ; idle, recording, playing, reconnecting
    "pythonPID", 0,
    "activeMode", "default",  ; Current macro mode name
    "configPath", A_ScriptDir "\..\config\settings.json"
)

; Core components
global gContainer := Container()
global gIPCClient := IPCClient()
global gMouseMode := MouseMode(gIPCClient)
global gRecorder := Recorder(gContainer, gMouseMode)

; Configuration (defaults, overridden by settings.json)
global Config := Map(
    "hotkeyRecord", "F6",
    "hotkeyStop", "F7",
    "hotkeyPlay", "F8",
    "hotkeyEmergencyStop", "F12",
    "recordingsDir", A_ScriptDir "\..\recordings",
    "presetsDir", A_ScriptDir "\..\presets",
    "pythonExe", "python",
    "pythonScript", A_ScriptDir "\..\python\main.py"
)

; ============================================================================
; Initialization
; ============================================================================

Main()

Main() {
    ; Load configuration
    LoadConfig()
    
    ; Launch the Python vision service (opens the two-panel GUI)
    LaunchPython()
    
    ; Wait briefly for Python to start the named pipe server
    Sleep(2000)
    
    ; Connect to Python IPC
    if (gIPCClient.Connect()) {
        ShowNotification("Vision Service", "Connected to Python vision service")
        ; Sync container from Python — GUI positions Roblox behind the container
        ; canvas and updates the rect via set_manual(). AHK reads it here.
        SyncContainerFromPython()
    } else {
        ShowNotification("Vision Service", "Python vision service not yet ready — will retry on first use")
    }
    
    ; Register global hotkeys
    RegisterHotkeys()
    
    ; Start mouse-mode monitoring
    gMouseMode.Start()
    
    ; Show ready notification
    ShowNotification("RenzeiMacro", 
        "Ready. Open Roblox and it will appear in the container. "
        "Press " Config["hotkeyRecord"] " to record.")
    
    ; Keep the script running
    Persistent()
}

; ============================================================================
; Hotkey Registration
; ============================================================================

RegisterHotkeys() {
    Hotkey(Config["hotkeyRecord"], OnRecord)
    Hotkey(Config["hotkeyStop"], OnStop)
    Hotkey(Config["hotkeyPlay"], OnPlay)
    Hotkey(Config["hotkeyEmergencyStop"], OnEmergencyStop)
}

; ============================================================================
; Hotkey Handlers
; ============================================================================

OnRecord(*) {
    if (AppState["mode"] != "idle") {
        ShowNotification("RenzeiMacro", "Cannot record — currently " AppState["mode"])
        return
    }
    
    ; Sync container from Python sidebar (Python owns the container rect)
    if (!gContainer.isValid)
        SyncContainerFromPython()
    
    if (!gContainer.isValid) {
        ShowNotification("Error", "Container not set. Use the RenzeiMacro sidebar to Snap Roblox first.")
        return
    }
    
    AppState["mode"] := "recording"
    
    try {
        gRecorder.StartRecording()
        ShowNotification("Recording", "Recording started... Press " Config["hotkeyStop"] " to stop.")
    } catch as e {
        AppState["mode"] := "idle"
        ShowNotification("Error", "Failed to start recording: " e.Message)
    }
}

OnStop(*) {
    if (AppState["mode"] = "recording") {
        events := gRecorder.StopRecording()
        AppState["mode"] := "idle"
        
        if (events.Length > 0) {
            ; Save the recording
            timestamp := FormatTime(, "yyyyMMdd_HHmmss")
            filePath := Config["recordingsDir"] "\recording_" timestamp ".json"
            
            ; Ensure directory exists
            if (!DirExist(Config["recordingsDir"]))
                DirCreate(Config["recordingsDir"])
            
            gRecorder.SaveRecording(filePath)
            ShowNotification("Recording Saved", "Saved " events.Length " events to " filePath)
        } else {
            ShowNotification("Recording", "No events captured.")
        }
    }
    else if (AppState["mode"] = "playing") {
        gIPCClient.StopMacro()
        AppState["mode"] := "idle"
        ShowNotification("Playback", "Playback stopped.")
    }
    else {
        ShowNotification("RenzeiMacro", "Nothing to stop.")
    }
}

OnPlay(*) {
    if (AppState["mode"] != "idle") {
        ShowNotification("RenzeiMacro", "Cannot play — currently " AppState["mode"])
        return
    }
    
    ; Find the most recent recording
    recordingFile := FindLatestRecording()
    if (!recordingFile) {
        ShowNotification("Error", "No recordings found in " Config["recordingsDir"])
        return
    }
    
    ; Sync container from Python sidebar
    if (!gContainer.isValid)
        SyncContainerFromPython()
    
    if (!gContainer.isValid) {
        ShowNotification("Error", "Container not set. Use the RenzeiMacro sidebar to Snap Roblox first.")
        return
    }
    
    AppState["mode"] := "playing"
    ShowNotification("Playback", "Playing: " recordingFile "`nPress " Config["hotkeyStop"] " to stop.")
    
    ; Ask Python to play the macro
    resp := gIPCClient.PlayMacro(recordingFile)
    if (resp.Has("error")) {
        AppState["mode"] := "idle"
        ShowNotification("Error", "Playback failed: " resp["error"])
    }
}

OnEmergencyStop(*) {
    ; Emergency stop — halt everything immediately
    gRecorder.StopRecording()
    gIPCClient.StopMacro()
    gMouseMode.Stop()
    AppState["mode"] := "idle"
    ShowNotification("EMERGENCY STOP", "All operations halted.")
    
    ; Restart mouse-mode monitoring
    gMouseMode.Start()
}

; ============================================================================
; SyncContainerFromPython
; ============================================================================
; Python owns the container rect (set via ClientToScreen after Snap Roblox).
; Ask Python for the current rect via IPC and update gContainer.
; Falls back to AutoDetect() if IPC is not yet connected.

SyncContainerFromPython() {
    ; Try IPC first
    if (gIPCClient.isConnected || gIPCClient.Connect()) {
        resp := gIPCClient.QueryContainer()
        if (resp.Has("valid") && resp["valid"]) {
            gContainer.SetManual(resp["x"], resp["y"], resp["w"], resp["h"])
            return true
        }
    }
    ; Fallback: detect directly (pre-snap or Python not running)
    return gContainer.AutoDetect()
}

; ============================================================================
; Python Vision Service Management
; ============================================================================

LaunchPython() {
    pythonScript := Config["pythonScript"]
    pythonExe := Config["pythonExe"]
    
    if (!FileExist(pythonScript)) {
        ShowNotification("Warning", "Python script not found: " pythonScript "`nVision features will be unavailable.")
        return
    }
    
    try {
        Run(pythonExe ' "' pythonScript '"', A_ScriptDir "\..\python", "Hide", &pid)
        AppState["pythonPID"] := pid
    } catch as e {
        ShowNotification("Warning", "Failed to launch Python vision service: " e.Message)
    }
}

; ============================================================================
; Configuration
; ============================================================================

LoadConfig() {
    configFile := AppState["configPath"]
    
    if (!FileExist(configFile))
        return  ; Use defaults
    
    try {
        content := FileRead(configFile, "UTF-8")
        parsed := gIPCClient._JsonToMap(content)
        
        ; Apply keybinds if present
        if (parsed.Has("keybinds")) {
            keybinds := parsed["keybinds"]
            if (keybinds.Has("record"))
                Config["hotkeyRecord"] := keybinds["record"]
            if (keybinds.Has("stop"))
                Config["hotkeyStop"] := keybinds["stop"]
            if (keybinds.Has("play"))
                Config["hotkeyPlay"] := keybinds["play"]
        }
        
        ; Apply active mode
        if (parsed.Has("active_mode"))
            AppState["activeMode"] := parsed["active_mode"]
    }
}

; ============================================================================
; Utility Functions
; ============================================================================

FindLatestRecording() {
    dir := Config["recordingsDir"]
    if (!DirExist(dir))
        return ""
    
    latestFile := ""
    latestTime := ""
    
    loop files, dir "\*.json" {
        if (!latestTime || A_LoopFileTimeModified > latestTime) {
            latestTime := A_LoopFileTimeModified
            latestFile := A_LoopFileFullPath
        }
    }
    
    return latestFile
}

ShowNotification(title, message) {
    ToolTip("[RenzeiMacro] " title ": " message)
    SetTimer(() => ToolTip(), -3000)  ; Auto-hide after 3 seconds
}

; ============================================================================
; Cleanup on exit
; ============================================================================

OnExit(ExitHandler)

ExitHandler(exitReason, exitCode) {
    ; Stop all operations
    gRecorder.StopRecording()
    gPlayer.Stop()
    gMouseMode.Stop()
    gIPCClient.Disconnect()
    
    ; Kill Python process
    if (AppState["pythonPID"]) {
        try ProcessClose(AppState["pythonPID"])
    }
}
