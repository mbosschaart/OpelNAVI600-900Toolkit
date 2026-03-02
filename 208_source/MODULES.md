# Navi 600/900 v2.08 — Firmware Module Reference

The Opel Navi 600/900 (GM-GE platform, Bosch) firmware consists of 13 XOZL-
compressed MIPS32 ELF modules plus a bootloader. Each module runs as a separate
process on the head unit's MIPS processor, communicating via a CCA (Component
Communication Architecture) message bus.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  dragon.bin                      BOOTLOADER                    │
│  ─ NAND flash management (FlashFX)                             │
│  ─ LZO decompression of XOZL modules                          │
│  ─ ELF segment loader                                          │
│  ─ USB download mode (firmware update)                         │
└──────────────────────┬──────────────────────────────────────────┘
                       │ loads
    ┌──────────────────┼──────────────────────────────┐
    │                  │                              │
    ▼                  ▼                              ▼
┌─────────┐    ┌────────────┐    ┌─────────────────────────────┐
│ sysprog  │    │  ProcBase   │    │  Application Processes       │
│ modules  │    │ (base svc)  │    │  ProcHMI, ProcNav, ProcSDS, │
│ (OSAL,   │    │             │    │  ProcMM, ProcMW, ProcMP1,   │
│  drivers)│    │             │    │  ProcMap                    │
└─────────┘    └────────────┘    └─────────────────────────────┘
```

---

## Bootloader

### dragon.bin (1.9 MB, 490K instructions)

The first-stage bootloader and flash management layer. This is a raw MIPS64
binary (not wrapped in XOZL), loaded directly from NAND.

**Primary functions:**
- **NAND flash management** — FlashFX filesystem, bad block management, wear
  leveling, SSFDC translation layer
- **Module loader** — Reads `.out` files from NAND, dispatches by magic:
  - `XOZL` → LZO1X decompress → load as ELF
  - `\x7fELF` → load directly as raw ELF
  - `ULI ` → resource archive handler
- **USB download mode** — Firmware update protocol ("Bosch Download tool")
- **Partition management** — Dual-partition failsafe boot (origin vs changed
  partition, error counters, fallback logic)
- **Hardware init** — DRAM setup, PLL/clock configuration, DMA controller init

**Key subsystems:**
- `FlashFX` / `FfxBbm` — NAND flash translation layer with bad block management
- `DMAC_r_K01` — DMA controller driver
- Module loading logic at `0x4ab50`–`0x4adb0`
- Decompression at `0x4ac40` (XOZL handler)

---

## System Programs (Low-Level)

### sysprogosalio (1.7 MB, 262K instructions)

**OSAL I/O Layer** — The operating system abstraction layer providing
hardware-independent APIs for all other modules.

**Primary functions:**
- **Thread management** — Thread creation, scheduling, priority control,
  kill/join (`Spawn Thread with prio: %d and sys load %d %%`)
- **Synchronization** — Mutexes, semaphores, message queues, event handling
- **Device drivers** — Flash (`/dev/flash`), NAND (`/dev/nand0`, `/dev/nand1`),
  auxiliary clock (`/dev/auxclock`)
- **File system** — NAND file I/O, file deletion, backup management
- **Hardware abstraction** — GPIO, PWM, ADC ("Dragon ADC/GPIO settings"),
  RTC time synchronization, cryptography (SHA1, OSAL_CRYPT)
- **System Power Management** — SPM watchdog, background task files,
  learned VIN storage

**Key classes:** `OSAL_*`, `DEV_SPM_OSALIO_*`, `RTC_vSynchronizeRDSTime`,
`BPCL_SHA1_Init`

### sysprog1 (2.2 MB, 424K instructions)

**Audio/Display Hardware Drivers** — Low-level drivers for the audio pipeline
and display subsystem.

**Primary functions:**
- **Audio codec drivers** — DAC, ADC, ASRC (asynchronous sample rate converter),
  I2S interface management, AMR decoder
- **DMA audio streaming** — `DACDR_setdacDMA`, `ADAC_DMA`, per-port DMA setup
- **SPI driver** — `SPI_r_L10` for peripheral communication
- **RTC driver** — `RTC_r_K01` real-time clock
- **Clock management** — `CLKTR_get_clock`, `CLKTR_set_clock`, `ClockDrv`
- **Display driver** — Font loading from flash (`/flashfont/`, `/nand0/fonts/`),
  display configuration (`/nand0/display.cfg`)
- **GPIO/mute control** — `AR_MUTE_GPIO` for amplifier muting
- **Audio main task** — `AUDDRV_main`

**Key subsystems:** `DCA_DT` (decoder task), `DCA_OT` (output task),
`DCA_SDI` (serial data interface)

### sysprog2 (1.5 MB, 243K instructions)

**Storage & Device Communication Middleware** — Block device drivers and
file system management.

**Primary functions:**
- **NAND/Flash driver** — Block-level NAND I/O, `DNCD_InitModul`, `DNFS_init_main`
- **FAT filesystem** — `FAT_main`, FAT16/FAT32 support for USB media
- **ATA/IDE driver** — `IDE_ata_read_dma`, `IDE_ata_write_dma`,
  `IDE_packet_dma_cmd` (CD/DVD interface)
- **DMA library** — `LIB_DMA_INIT`, `LIB_DMA_SETUP`, `LIB_DMA_START`,
  `LIB_DMA_STOP`, `LIB_DMA_CLEAR`
- **GPIO** — `GPIO_read`, `GPIO_setup`, `GPIO_write`
- **OSAL services** — Shared memory (`OSAL_SharedMemoryCreate`),
  clock (`OSAL_ClockGetElapsedTime`), I/O (`OSAL_IOOpen`)
- **DCMOD main** — Device control module entry point (`DCMOD_main`)
- **Masca driver** — `MascaDrv` (Bosch-specific audio DSP interface)
- **Event management** — `CFW_util_evm_*` event system

### sysprogcal (454 KB, 79K instructions)

**Calibration & Audio Routing** — Audio signal routing calibration and
low-level audio connection management.

**Primary functions:**
- **Audio calibration** — `vStartCalDriverFunction`, `ar_CalIfcSetDriverUnit`
- **Audio stream routing** — `ar_DacStreamSetDriverSubUnit`,
  `ar_EdarStreamSetDriverSubUnit`
- **Connection management** — `STM_post_event_conn_added`,
  `STM_post_event_conn_changed`, `STM_post_event_conn_dropped`
- **Event/state machine** — `EVM_api_*`, `STM_api_*` event and state APIs
- **Registry** — `u32InitRegDataBase`, `REG_callback_event_notif`
- **POSIX threading** — Direct pthread mutex usage (low-level module)

### sysprogperf (395 KB, 49K instructions)

**Performance Monitoring & Diagnostics** — System profiling, task monitoring,
and watchdog support.

**Primary functions:**
- **Task profiling** — Runtime data, sleep time, interrupt time, latency
  measurement per task (`PA_tclTaskRuntimeData`, `PA_tclAvgTaskLatency`,
  `PA_tclMaxTaskLatency`)
- **System history** — `PA_tclSystemHistory`, `PA_tclSysOperatingTimer`
- **Memory monitoring** — `PA_tclTaskMemoryData`
- **Handle/resource watch** — `PA_tclHandleWatch`
- **Output formatting** — `PA_tclPrinter`, `PA_tclFloatFormatter`,
  `PA_tclOutputFormatter` (for diagnostic output)
- **Watchdog** — `/nand0/debug_watchp.bin`, exception handler config
  (`/nand0/exchand.cnf`)
- **Backup/logging** — `PA_tclBackupFile`, `PA_tclRingbufferFiles`,
  `PA_tclUnlimitedFile`

---

## Application Processes (High-Level)

### ProcBase (5.6 MB, 1.0M instructions)

**Base Services Framework** — The foundation application layer providing
the CCA/CSFW communication framework and core system services.

**Primary functions:**
- **CSFW (Component Software Framework)** — `CSFWBaseApp` (62 methods),
  `CSFWCCABaseApp` (25 methods) — the application lifecycle and messaging
  framework used by all Proc modules
- **CCA (Component Communication Architecture)** — `CCAReceiverApp`,
  `ChannelRequest` — inter-process message routing
- **Security/anti-theft** — `DCM_Security` (26 methods) — theft detection,
  valet mode, security authentication states
- **Audio amplifier control** — `DCM_Amplifier`, `DCM_ADRAmplifier`,
  `DCM_PWMAmplifier`, `AMP_ADRSoundSettingsMsgHandler`, `AMP_ChimeHandler`
- **Diagnostics client** — `dia_tclSpmClientHandler` — SPM (System Power
  Management) diagnostics
- **Tuner message handling** — `xmtun_tcl_SysMsgHandler`,
  `xmtun_tcl_CBMRx_ThreadApp` (radio tuner interface)
- **VDL (Vehicle Data Link)** — `vdl_tclCSFWApp` — CAN bus vehicle data

### ProcHMI (15.6 MB, 2.5M instructions)

**Human-Machine Interface** — The main user-facing application handling all
screen rendering, user input, and device coordination. This is the largest
module and the target of the iPod auth retry patch.

**Primary functions:**
- **iPod/iPhone control** — `iPodCtrlCoordinator`, `IAPInterface`,
  `iPod_cmd_connect`, `iPod_cmd_disconnect` — MFi authentication, iAP protocol,
  media browsing. **This is where the auth retry patch is applied.**
- **USB device management** — `DCM_USB` (36 methods), `DCM_USB_FAT`,
  `DCM_USB_FAT16`, `DCM_USB_FAT32`, `DCM_USB_FS_parser`, `DCMUSB_StHa`
- **AUX jack** — `AuxJackServerHandler`, `AuxJack_DeviceState`
- **Audio source arbitration** — `AudioSourceServerHandler` (21 methods)
- **Bluetooth** — Bluetooth device management, handsfree, A2DP
- **Navigation UI** — `enavi_tclRouteList1`, `enavi_tclTrip` — route display,
  trip computer, turn-by-turn rendering
- **Address input** — `ADDRINPUT` — destination entry screens
- **Screen/display** — Screen management, popup handling, menu systems,
  dynamic lists, scrolling
- **CD/DVD media** — `Dcmodw_CDTextParser`, `Dcmodw_Coordinator`,
  `Dcmodw_PRMClient`
- **Phone integration** — Contacts, handsfree, phone UI screens
- **Customization** — `Customization_ObjectComputationSettings`
- **Datapools** — Shared data storage for HMI state (connection status,
  media info, trip data)

### ProcNav (16.5 MB, 3.0M instructions)

**Navigation Engine** — The core navigation computation engine. Largest module
by instruction count.

**Primary functions:**
- **Route calculation** — `tclGraph`, route planning, turn-by-turn guidance
  (`rg_tcl_FunctionalityStatesProperty`)
- **Map database** — Map data loading, section buffering
  (`pc_tclLdbSectionBuffer`), data table management
- **Position computation** — `pc_tclSetPositionByLocation`, dead reckoning (DR)
- **TMC traffic** — `tmc_tclCommonLocationDesc`, `tmc_tclPointLocation` —
  real-time traffic message channel processing
- **Navigation interface** — `NaviFI`, `IntNaviFI` — internal navigation
  function interfaces
- **Resource info** — `ResInf` — map resource management, MDB (Map DataBase)
  property interface
- **Archive management** — `ArchivePccompIni` — compressed map archive handling
- **Road attributes** — Road conditions, load prohibitions, dimensions
  (AN_TYPE_* constants for vehicle restriction matching)
- **Route guidance** — `rg_tcl_*` — active guidance state machine, maneuver
  computation, turn info (`tclTurnToInfo`)

### ProcSDS (6.3 MB, 1.2M instructions)

**Speech Dialog System** — Voice recognition and text-to-speech engine.

**Primary functions:**
- **Voice recognition** — `fc_recog_tclRecogThread` (16 methods),
  `fc_recog_tclMainThread`, `fc_recog_tclDestInpThread` — acoustic input
  processing, recognition engine, destination input by voice
- **Voice control** — `fc_VoiceControl_tclEngineThread` (22 methods),
  `fc_VoiceControl_tclMainThread` (65 methods),
  `fc_VoiceControl_tclWorkThread` (39 methods) — voice command handling
- **ASR (Automatic Speech Recognition)** — `SDS_tclASR` (39 methods),
  `SDS_tclASRParam`, `SDS_tclASRBase`
- **Prompt player (TTS)** — `fc_PPlayer_tclMainThread` (72 methods),
  `fc_PPlayer_tclPackageContainer` (42 methods) — text-to-speech audio
  playback, voice prompt management
- **Grammar/dictionary** — `SDS_tclGrammar` (19 methods),
  `SDS_tclDictionary`, `SDS_tclSpelltree`, `SDS_tclUserWord`
- **Audio I/O** — `SDS_tclAudio` (17 methods) — microphone input,
  speaker output for voice dialogs
- **Context management** — `SDS_tclContext` (25 methods) — dialog state,
  active grammar sets
- **Data packaging** — `SDS_tclDataPackage`, `SDS_tclMetaFile`,
  `SDS_tclFile` — voice data file management, SDP downloads

### ProcMW (4.8 MB, 832K instructions)

**Middleware & Diagnostics** — Diagnostic services, voice control integration,
and system-level middleware.

**Primary functions:**
- **Diagnostics (UDS/OBD)** — Extensive UDS (Unified Diagnostic Services)
  implementation:
  - `dia_tclDiagSessionUds` (32 methods) — diagnostic session management
  - `dia_tclDiagSessionMcnet` — MCNET diagnostic sessions
  - `dia_tclSpmClientHandler` (18 methods) — system power management
  - `dia_tclDiagLogClientHandler` (16 methods) — diagnostic logging
  - DID read/write handlers for VIN, manufacturing data, enable counters
- **ACR (Audio Content Recognition)** — `DCM_ACRHandler` (21+ methods),
  `ACR_AudioMgmt_Handler`, `ACR_StateMachine`, `ACR_RequestQueue`
- **CCA client handlers** — Dozens of diagnostic CCA clients:
  - `dia_tclCLOCKClientHandler` — clock/time
  - `dia_tclDIMMINGClientHandler` — display dimming
  - `dia_tclFCTunerClientHandler` — tuner diagnostics
  - `dia_tclHeatCtrlClientHandler` — climate control interface
  - `dia_tclKBDClientHandler` — keyboard/buttons
  - `dia_tclVDAmFmAudClientHandler` — AM/FM audio diagnostics
  - `dia_tclUAMClientHandler` — user access management
  - `dia_tclCSFWSecurityClientHandler` — security diagnostics
- **Voice prompt control** — `CCAClientHandler_PromptPlayerHandler`,
  `CCAClientHandler_VoiceCtrlHandler`, `CCAClientHandler_VoiceHandler`
- **USB diagnostics** — `diagnostics_USB_comms`
- **RVC (Rear View Camera)** — `dia_tclCCA_RvcClientHandler`

### ProcMM (4.0 MB, 754K instructions)

**Multimedia & Audio Master** — Media browsing and audio source management.

**Primary functions:**
- **Media browser** — `DCM_Browser` (37 methods), `Browser_Data`,
  `Browser_LastMode` — file/folder browsing on USB, CD, SD media
- **Audio master** — `DCM_Audiomaster` — audio source selection and routing
- **Service app mapping** — `scd_tclServiceAppMap` (7 methods) — service
  discovery and application routing

### ProcMP1 (1.6 MB, 296K instructions)

**Media Player** — Core media playback engine for USB/CD/DVD content.

**Primary functions:**
- **Media playback** — `MediaDeviceMediaplayer` (45+ error handlers, 14 debug,
  6 info) — the main media playback state machine
- **Media device callbacks** — `MediaDeviceCallbackHandler` — device attach/
  detach/status event handling (analogous to iPod callbacks in ProcHMI)
- **Mediaplayer coordinator** — `MediaplayerCoordinator` — playback state
  coordination, queue management
- **Audio connection** — `AudioConnectionHandler` — audio output routing
- **Message dispatch** — `mp_dispatcher` (24 error, 2 fatal) — media player
  command routing
- **Database handling** — `DatabaseHandlingTask` — media metadata database
- **DCM interface** — `DCM_Mediaplayer`, `DcmodClient`

### ProcMap (2.4 MB, 458K instructions)

**Map Rendering Engine** — Map display, scrolling, POI rendering, and
data management for the navigation map view.

**Primary functions:**
- **Map rendering** — `rl_tclRenderLayer`, `RenFI` (render function interface),
  `MapAnimationManager`, `MapView`
- **Map engine control** — `map_tclMapEngineControl`, `MapEngineConfig`,
  `MapConfig`
- **Map data management** — `MDM` (63 methods), `MDMLoadBlocks`,
  `map_tclMapDataManager`, `MapDataManager`
- **Port control** — `map_tclPortControl`, `PortControl_Platform` (28 methods),
  `PortControl_CCAClientHandler_DAPI` (16 methods),
  `PortControl_CCAClientHandler_Navi` (14 methods) — map viewport management
- **POI rendering** — `MapElm_Special_POI` — point-of-interest icons on map
- **Scrolling** — `map_tclApplySettingsBuffer_ContinuousScrolling`
- **Memory management** — `MemoryManager CellPool`, `MemoryManager MemPool` —
  tile/cell memory management for map rendering
- **Job queue** — `JobQueue` (11+ methods) — asynchronous map rendering tasks
- **Localisation** — `LocalisationManager`, `DynamicElementManager`

---

## Module Communication

All application modules communicate via the **CCA (Component Communication
Architecture)** message bus. Key patterns visible in the code:

- **CCAReceiverApp** — Present in ProcBase, ProcHMI, ProcMap (message receivers)
- **CSFWBaseApp** (62 methods) — Present in every Proc module (base class)
- **CSFWCCABaseApp** (25 methods) — CCA-enabled application base class
- **scd_tclServiceAppMap** — Service discovery (in ProcMM, ProcMP1)
- **DCM_* classes** — Device Control Modules providing hardware abstraction
- **dia_tcl* classes** — Diagnostic handlers for each subsystem

## Summary Table

| Module | Size | Purpose | Key Subsystems |
|--------|------|---------|----------------|
| dragon.bin | 1.9 MB | Bootloader | NAND, FlashFX, LZO, ELF loader, USB update |
| sysprogosalio | 1.7 MB | OS abstraction layer | Threads, mutexes, file I/O, GPIO, RTC, crypto |
| sysprog1 | 2.2 MB | Audio/display HW drivers | DAC, ADC, ASRC, I2S, DMA, SPI, fonts |
| sysprog2 | 1.5 MB | Storage middleware | NAND, FAT, ATA/IDE, DMA, block devices |
| sysprogcal | 454 KB | Audio calibration | Audio routing, stream calibration, connections |
| sysprogperf | 395 KB | Performance monitoring | Task profiling, latency, memory, watchdog |
| ProcBase | 5.6 MB | Base services framework | CSFW, CCA, security, amplifier, tuner, VDL |
| ProcHMI | 15.6 MB | User interface | iPod/USB, Bluetooth, nav UI, phone, menus |
| ProcNav | 16.5 MB | Navigation engine | Routing, map DB, TMC, position, guidance |
| ProcSDS | 6.3 MB | Speech dialog system | Voice recognition, TTS, grammars, ASR |
| ProcMW | 4.8 MB | Middleware/diagnostics | UDS, ACR, diagnostic CCA handlers |
| ProcMM | 4.0 MB | Multimedia | Media browser, audio master |
| ProcMP1 | 1.6 MB | Media player | Playback engine, device callbacks, database |
| ProcMap | 2.4 MB | Map renderer | Tile rendering, MDM, POI, scrolling, viewports |
