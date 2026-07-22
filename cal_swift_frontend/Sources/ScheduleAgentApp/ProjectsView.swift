import SwiftUI

/// Projects dashboard, presented as a sheet from the sidebar. A NavigationStack
/// drives list → detail (the main window is a borderless hover panel, so a
/// stack lives more cleanly inside a sheet than in the panel itself).
struct ProjectsView: View {
    @StateObject private var model = ProjectsViewModel()
    @Environment(\.dismiss) private var dismiss

    @State private var isCreating = false
    @State private var newProjectName = ""

    var body: some View {
        NavigationStack {
            Group {
                if model.isLoading && model.projects.isEmpty {
                    ProgressView("加载项目…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if model.projects.isEmpty {
                    emptyState
                } else {
                    projectList
                }
            }
            .navigationTitle("项目")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("完成") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        newProjectName = ""
                        isCreating = true
                    } label: {
                        Label("新建项目", systemImage: "plus")
                    }
                }
            }
        }
        .frame(minWidth: 460, minHeight: 560)
        .task { await model.loadProjects() }
        .alert("新建项目", isPresented: $isCreating) {
            TextField("项目名称", text: $newProjectName)
            Button("创建") { Task { await model.createProject(name: newProjectName) } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("给这个项目起个名字，之后可以往里导入计划。")
        }
        .alert("出错了", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("好", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "folder.badge.plus")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)
            Text("还没有项目")
                .font(.headline)
            Text("新建一个项目，然后从课程大纲、PRD 或一段文字导入计划。")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("新建项目") {
                newProjectName = ""
                isCreating = true
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var projectList: some View {
        List {
            ForEach(model.projects) { project in
                NavigationLink {
                    ProjectDetailView(project: project, model: model)
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(project.name)
                            .font(.system(size: 14, weight: .semibold))
                        HStack(spacing: 8) {
                            if let status = project.status {
                                Text(status).foregroundStyle(.secondary)
                            }
                            if let deadline = project.deadline {
                                Text("截止 \(deadline)").foregroundStyle(.secondary)
                            }
                            if let count = project.taskIDs?.count, count > 0 {
                                Text("\(count) 个任务").foregroundStyle(.secondary)
                            }
                        }
                        .font(.caption)
                    }
                    .padding(.vertical, 2)
                }
            }
            .onDelete { indexSet in
                let targets = indexSet.map { model.projects[$0] }
                Task { for p in targets { await model.deleteProject(p) } }
            }
        }
    }
}
