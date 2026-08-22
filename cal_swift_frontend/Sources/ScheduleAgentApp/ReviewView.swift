import SwiftUI
import ScheduleAgentCore

/// Completion review: a GitHub-style "commit wall" heatmap over the last ~120
/// days plus recent completions. All numbers come from the backend's
/// completion_store (via /completions/heatmap + /completions) — this view never
/// computes progress from anything the LLM said.
struct ReviewView: View {
    private let client = DayflowAPIClient()

    @State private var heatmap: DayflowHeatmap?
    @State private var completions: [DayflowCompletionRecord] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    private let green = Color(nsColor: .systemGreen)

    // Date-only formatter + Monday-first calendar for the grid.
    private static let ymd: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    private static var cal: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.firstWeekday = 2   // Monday
        return c
    }()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("完成复盘")
                    .font(.system(size: 20, weight: .semibold))

                if isLoading {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Loading…").foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else if let errorMessage {
                    Text(errorMessage).foregroundStyle(.secondary)
                } else if let heatmap {
                    summary(heatmap)
                    heatmapSection(heatmap)
                    recentSection
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await load() }
    }

    // MARK: load

    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            async let hm = client.fetchHeatmap()
            async let cs = client.listCompletions()
            let (h, c) = try await (hm, cs)
            heatmap = h
            completions = c.completions
                .filter { $0.status == "done" }
                .sorted { ($0.completedAt ?? "") > ($1.completedAt ?? "") }
        } catch {
            errorMessage = "Couldn't load review: \(error.localizedDescription)"
        }
        isLoading = false
    }

    // MARK: summary

    private func summary(_ hm: DayflowHeatmap) -> some View {
        let total = hm.counts.values.reduce(0, +)
        let activeDays = hm.counts.values.filter { $0 > 0 }.count
        let streak = currentStreak(hm)
        return HStack(spacing: 12) {
            statTile("\(total)", "已完成")
            statTile("\(activeDays)", "活跃天数")
            statTile("\(streak)", "当前连续")
        }
    }

    private func statTile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value).font(.system(size: 24, weight: .bold))
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.primary.opacity(0.05)))
    }

    // MARK: heatmap

    private func heatmapSection(_ hm: DayflowHeatmap) -> some View {
        let weeks = buildWeeks(hm)
        let maxCount = hm.counts.values.max() ?? 0
        return VStack(alignment: .leading, spacing: 10) {
            Text("最近 120 天").font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 3) {
                    ForEach(Array(weeks.enumerated()), id: \.offset) { _, col in
                        VStack(spacing: 3) {
                            ForEach(Array(col.enumerated()), id: \.offset) { _, cell in
                                cellView(cell, maxCount: maxCount)
                            }
                        }
                    }
                }
            }
            legend
        }
    }

    private func cellView(_ cell: DayCell?, maxCount: Int) -> some View {
        let color: Color = cell == nil
            ? .clear
            : cellColor(level(cell!.count, max: maxCount))
        return RoundedRectangle(cornerRadius: 2)
            .fill(color)
            .frame(width: 12, height: 12)
            .help(cell.map { "\($0.key): \($0.count) 完成" } ?? "")
    }

    private var legend: some View {
        HStack(spacing: 5) {
            Text("少").font(.caption2).foregroundStyle(.secondary)
            ForEach(0...4, id: \.self) { lvl in
                RoundedRectangle(cornerRadius: 2).fill(cellColor(lvl)).frame(width: 11, height: 11)
            }
            Text("多").font(.caption2).foregroundStyle(.secondary)
        }
    }

    // MARK: recent

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("最近完成").font(.headline)
            if completions.isEmpty {
                Text("还没有完成记录。勾掉任务卡右侧的 ✓ 就会出现在这里。")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                ForEach(completions.prefix(40)) { rec in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(green)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(rec.title).font(.system(size: 13, weight: .medium)).lineLimit(2)
                            if let d = rec.scheduledDate {
                                Text(d).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                    }
                    .padding(.vertical, 4)
                    Divider()
                }
            }
        }
    }

    // MARK: helpers

    private struct DayCell { let date: Date; let key: String; let count: Int }

    private func buildWeeks(_ hm: DayflowHeatmap) -> [[DayCell?]] {
        guard let start = Self.ymd.date(from: hm.from),
              let end = Self.ymd.date(from: hm.to) else { return [] }
        let cal = Self.cal
        let startWeekday = cal.component(.weekday, from: start)   // 1=Sun..7=Sat
        let back = (startWeekday + 5) % 7                          // Monday=0
        guard let alignedStart = cal.date(byAdding: .day, value: -back, to: start) else { return [] }

        var cells: [DayCell?] = []
        var d = alignedStart
        while d <= end {
            let key = Self.ymd.string(from: d)
            cells.append(d >= start ? DayCell(date: d, key: key, count: hm.counts[key] ?? 0) : nil)
            guard let next = cal.date(byAdding: .day, value: 1, to: d) else { break }
            d = next
        }
        while cells.count % 7 != 0 { cells.append(nil) }
        return stride(from: 0, to: cells.count, by: 7).map { Array(cells[$0 ..< $0 + 7]) }
    }

    private func currentStreak(_ hm: DayflowHeatmap) -> Int {
        guard let end = Self.ymd.date(from: hm.to) else { return 0 }
        let cal = Self.cal
        var d = end
        // Allow today to still be empty without breaking a streak built up to yesterday.
        if (hm.counts[Self.ymd.string(from: d)] ?? 0) == 0 {
            guard let y = cal.date(byAdding: .day, value: -1, to: d) else { return 0 }
            d = y
        }
        var streak = 0
        while (hm.counts[Self.ymd.string(from: d)] ?? 0) > 0 {
            streak += 1
            guard let prev = cal.date(byAdding: .day, value: -1, to: d) else { break }
            d = prev
        }
        return streak
    }

    private func level(_ count: Int, max: Int) -> Int {
        if count <= 0 { return 0 }
        if max <= 1 { return 4 }
        let r = Double(count) / Double(max)
        if r <= 0.25 { return 1 }
        if r <= 0.5 { return 2 }
        if r <= 0.75 { return 3 }
        return 4
    }

    private func cellColor(_ level: Int) -> Color {
        switch level {
        case 1: return green.opacity(0.30)
        case 2: return green.opacity(0.50)
        case 3: return green.opacity(0.72)
        case 4: return green.opacity(0.95)
        default: return Color.primary.opacity(0.08)
        }
    }
}
