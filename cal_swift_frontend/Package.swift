// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "ScheduleAgent",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "ScheduleAgentCore", targets: ["ScheduleAgentCore"]),
        .executable(name: "ScheduleAgentApp", targets: ["ScheduleAgentApp"])
    ],
    targets: [
        .target(name: "ScheduleAgentCore"),
        .executableTarget(
            name: "ScheduleAgentApp",
            dependencies: ["ScheduleAgentCore"],
            linkerSettings: [
                // Embed Info.plist into the Mach-O so macOS TCC can read the
                // EventKit usage-description strings (a bare SwiftPM executable
                // has no bundle). Without this, Calendar/Reminders access is
                // denied outright. Path is relative to the package root.
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Info.plist",
                ])
            ]
        ),
        .testTarget(
            name: "ScheduleAgentCoreTests",
            dependencies: ["ScheduleAgentCore"]
        )
    ]
)
