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
            dependencies: ["ScheduleAgentCore"]
        ),
        .testTarget(
            name: "ScheduleAgentCoreTests",
            dependencies: ["ScheduleAgentCore"]
        )
    ]
)
