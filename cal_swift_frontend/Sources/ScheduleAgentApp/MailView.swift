import SwiftUI
import ScheduleAgentCore

/// The agent's triage of today's new mail. "Pull latest" runs the backend
/// triage over the user's attached email MCP server. Read-only: detected
/// invites are listed, not auto-added — ask the schedule agent to add one.
struct MailView: View {
    private let client = DayflowAPIClient()

    @State private var report: DayflowEmailReport?
    @State private var isLoading = false
    @State private var errorMessage: String?

    private static let ymd: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Text("今日邮件")
                        .font(.system(size: 20, weight: .semibold))
                    Spacer()
                    Button {
                        pull()
                    } label: {
                        HStack(spacing: 6) {
                            if isLoading { ProgressView().controlSize(.small) }
                            Image(systemName: "arrow.clockwise")
                            Text("拉取最新")
                        }
                    }
                    .disabled(isLoading)
                }

                if let errorMessage {
                    Text(errorMessage).foregroundStyle(.secondary)
                }

                if let report {
                    reportBody(report)
                } else if !isLoading {
                    Text("点「拉取最新」让 agent 整理今天新收到的邮件。")
                        .foregroundStyle(.secondary)
                }
            }
            .padding(20)
        }
        .onAppear { if report == nil { pull() } }
    }

    @ViewBuilder
    private func reportBody(_ r: DayflowEmailReport) -> some View {
        if !r.connected {
            VStack(alignment: .leading, spacing: 8) {
                Label("未连接邮箱", systemImage: "envelope.badge.shield.half.filled")
                    .font(.headline)
                if let note = r.note {
                    Text(note).foregroundStyle(.secondary).font(.callout)
                }
            }
        } else {
            if !r.summary.isEmpty {
                Text(r.summary)
                    .font(.callout)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            section("需要回复", systemImage: "arrowshape.turn.up.left", items: r.needsReply, tint: .orange)
            section("会议 / 面试 / 笔试邀请", systemImage: "calendar.badge.plus", items: r.detectedInvites, tint: .blue)
            section("订阅 / 资讯", systemImage: "newspaper", items: r.newsletters, tint: .secondary)
            if r.filteredAds > 0 {
                Text("已过滤 \(r.filteredAds) 封广告 / 推广")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let note = r.note {
                Text(note).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func section(_ title: String, systemImage: String, items: [DayflowEmailItem], tint: Color) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Label("\(title) (\(items.count))", systemImage: systemImage)
                    .font(.headline)
                    .foregroundStyle(tint)
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.subject)
                            .font(.system(size: 13, weight: .medium))
                            .fixedSize(horizontal: false, vertical: true)
                        if let sender = item.sender {
                            Text(sender).font(.caption).foregroundStyle(.secondary)
                        }
                        if let gist = item.gist {
                            Text(gist)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(RoundedRectangle(cornerRadius: 9).fill(Color.secondary.opacity(0.08)))
                }
            }
        }
    }

    private func pull() {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        let date = Self.ymd.string(from: Date())
        Task {
            do {
                let r = try await client.triageEmail(date: date)
                await MainActor.run {
                    report = r
                    isLoading = false
                }
            } catch {
                await MainActor.run {
                    errorMessage = "拉取失败：\(error.localizedDescription)"
                    isLoading = false
                }
            }
        }
    }
}
