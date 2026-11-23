let OVERVIEW_PROJECTS = [];

function overviewIcon(kind) {
  const paths = {
    project: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 5V3h8v2M8 10h8M8 14h5M8 18h4"/>',
    pending: '<rect x="4" y="4" width="13" height="16" rx="2"/><path d="M8 9h5M8 13h3"/><circle cx="17" cy="17" r="4"/><path d="M17 15v2l1 1"/>',
    flow: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M14 6h4v5M10 18H6v-5M14 3l3 3-3 3"/>',
    check: '<path d="M8 4H5a2 2 0 0 0-2 2v14h16v-6M9 10l4 4L22 4"/>',
    version: '<rect x="8" y="3" width="12" height="15" rx="2"/><path d="M4 7v14h12M12 7h4M12 11h4"/>',
    archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v12h14V9M9 13h6"/>',
    report: '<path d="M5 3h10l4 4v14H5zM14 3v5h5M9 17v-4M12 17v-7M15 17v-5"/>',
    drawing: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 17V7h10M7 13h6v4M11 7v3M17 7v3"/>'
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[kind] || paths.project}</svg>`;
}

function overviewBuildings() {
  return `<svg class="overview-buildings" viewBox="0 0 300 150" fill="none" aria-hidden="true">
    <defs><linearGradient id="obFront" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#edf6ff"/><stop offset="1" stop-color="#acd0fc"/></linearGradient><linearGradient id="obSide"><stop stop-color="#83b6f7"/><stop offset="1" stop-color="#bddbff"/></linearGradient></defs>
    <path d="M7 127 183 145 292 119 126 104Z" fill="#a9cefa" opacity=".4"/>
    <path d="m27 117 51 6V39l-51-5Z" fill="url(#obFront)"/><path d="m78 123 21-9V30l-21 9Z" fill="url(#obSide)"/><path d="m27 34 22-8 50 4-21 9Z" fill="#d5e9ff"/>
    <path d="m112 126 77 8V68l-77-8Z" fill="url(#obFront)"/><path d="m189 134 29-12V55l-29 13Z" fill="url(#obSide)"/><path d="m112 60 30-12 76 7-29 13Z" fill="#e6f2ff"/>
    <path d="m215 123 37 4V47l-37-4Z" fill="url(#obFront)"/><path d="m252 127 17-8V39l-17 8Z" fill="url(#obSide)"/><path d="m215 43 18-7 36 3-17 8Z" fill="#cee4ff"/>
    <g stroke="#75a9e8" stroke-width="3" opacity=".55"><path d="m37 48 8 1m10 1 8 1M37 63l8 1m10 1 8 1M37 78l8 1m10 1 8 1M37 93l8 1m10 1 8 1M124 75l12 1m12 1 12 1m12 1 8 1M124 91l12 1m12 1 12 1m12 1 8 1M124 107l12 1m12 1 12 1m12 1 8 1M225 59l17 2m-17 13 17 2m-17 13 17 2m-17 13 17 2"/></g>
    <path d="M102 49V24h73v28M137 24V12M128 12h19M95 24h89" stroke="#a3c9f7" stroke-width="2"/><path d="M173 24v19" stroke="#a3c9f7"/>
    <rect x="82" y="79" width="42" height="45" rx="5" fill="#f6fbff" stroke="#b1d1f8"/><path d="m92 103 7 7 15-18" stroke="#6ba5eb" stroke-width="3"/>
  </svg>`;
}

async function loadAgentOverview() {
  try {
    const response = await fetch('/api/agent/overview');
    if (!response.ok) throw new Error('项目概览读取失败');
    renderAgentOverview((await response.json()).projects);
  } catch (error) {
    document.getElementById('agentDashboard').innerHTML = '<div class="overview-empty">项目数据暂时无法读取。<button type="button" onclick="loadAgentOverview()">重新加载</button></div>';
  }
}

function renderAgentOverview(projects) {
  OVERVIEW_PROJECTS = projects;
  const sum = key => projects.reduce((n, p) => n + Number(p[key] || 0), 0);
  const current = projects.find(p => p.name === PROJECT) || projects[0];
  const index = current ? projects.indexOf(current) : -1;
  const empty = { stats: { total: 0, by_status: {} }, pending: 0, in_progress: 0, completed: 0,
    version_count: 0, latest_version: '—', archive_count: 0, archive_files: 0, reports_today: 0, reports_month: 0, workflow: [false, false, false, false] };
  const p = current || empty;
  const header = (title, view) => `<header class="overview-panel-head"><h3>${title}</h3><button type="button" class="overview-text-button" data-overview-view="${view}">全部 <span aria-hidden="true">›</span></button></header>`;
  const metric = (label, value, icon, color) => `<div class="overview-metric"><span class="overview-icon ${color}">${overviewIcon(icon)}</span><div><span>${label}</span><strong>${value}</strong></div></div>`;
  const workflow = [['drawing', '图纸登记'], ['flow', '变更识别'], ['project', '台账生成'], ['report', '报告输出']];
  document.getElementById('agentDashboard').innerHTML = `
    <div class="overview-top">
      <section class="overview-welcome"><div><h2>你好，欢迎回来</h2><p>智鉴云 Agent 为你管理图纸变更，<br>让每次调整都有据可查。</p></div>${overviewBuildings()}</section>
      ${metric('项目总数', projects.length, 'project', 'blue')}
      ${metric('待确认签证', sum('pending'), 'pending', 'coral')}
      ${metric('流转中签证', sum('in_progress'), 'flow', 'green')}
      ${metric('已索赔签证', sum('completed'), 'check', 'blue')}
    </div>
    <div class="overview-middle">
      <section class="overview-panel overview-projects"><header class="overview-panel-head"><div class="overview-heading"><h3>我的项目</h3><button type="button" class="overview-outline-button" onclick="document.getElementById('newProjectDialog').showModal()"><span aria-hidden="true">＋</span> 新建项目</button></div><span class="overview-meta">${projects.length} 个项目</span></header>
        <div class="overview-table-scroll"><table><thead><tr><th>项目名称</th><th>阶段</th><th>合同类型</th><th title="按签证单从待确认到已索赔的状态计算">签证流转进度</th><th>更新时间</th><th>操作</th></tr></thead><tbody>
        ${projects.map((project, i) => `<tr class="${current === project ? 'is-current' : ''}"><td><button type="button" class="overview-project-name" data-overview-project="${i}">${esc(project.name)}</button>${current === project ? '<span class="overview-current">当前</span>' : ''}</td><td><span class="overview-stage ${project.stage === '施工阶段' ? 'construction' : ''}">${esc(project.stage)}</span></td><td>${esc(project.contract)}</td><td><div class="overview-progress"><progress max="100" value="${project.progress}" aria-label="签证流转进度 ${project.progress}%"></progress><span>${project.progress}%</span></div></td><td class="overview-date">${esc((project.updated || '').slice(0, 16) || '—')}</td><td><div class="overview-project-actions"><button type="button" data-overview-project="${i}" data-open-view="workflow">进入</button><button type="button" data-overview-project="${i}" data-open-view="ledger">查看台账</button></div></td></tr>`).join('') || '<tr><td colspan="6" class="overview-empty">还没有项目，点击“新建项目”开始管理图纸变更。</td></tr>'}
        </tbody></table></div>
        <div class="overview-project-note">进度按签证流转状态统计；点击项目名称可切换下方概览。</div>
      </section>
      <section class="overview-panel overview-workflow">${header('Agent 工作流', 'workflow')}
        <p class="overview-context">${current ? esc(current.name) : '选择项目后查看处理进展'}</p>
        <div class="overview-flow">${workflow.map(([icon, label], i) => `<div class="overview-flow-step ${p.workflow[i] ? 'done' : ''}"><span class="overview-flow-icon">${overviewIcon(icon)}</span><strong>${label}</strong><span>${p.workflow[i] ? '已有记录' : '待处理'}</span></div>`).join('')}</div>
        <div class="overview-flow-footer"><button type="button" class="overview-outline-button" data-overview-view="workflow">查看详情</button><span class="overview-flow-hint">登记 · 比对 · 归档 · 跟踪</span></div>
      </section>
    </div>
    <div class="overview-section-label">${current ? `${esc(current.name)} <span>项目数据概览</span>` : '项目数据概览'}</div>
    <div class="overview-bottom">
      <section class="overview-panel overview-ledger">${header('变更台账', 'ledger')}<div class="overview-ledger-metrics">
        ${[['签证总数', p.stats.total, 'project', 'blue'], ['待确认', p.pending, 'pending', 'amber'], ['流转中', p.in_progress, 'flow', 'blue'], ['已索赔', p.completed, 'check', 'green']].map(([label, value, icon, color]) => `<button type="button" class="overview-ledger-stat" data-overview-view="ledger"><span class="overview-icon ${color}">${overviewIcon(icon)}</span><strong>${value}</strong><span>${label}</span></button>`).join('')}
      </div></section>
      <section class="overview-panel">${header('版本管理', 'versions')}<dl class="overview-facts"><div><dt>最近登记版本</dt><dd>${esc(p.latest_version)}</dd></div><div><dt>图纸版本数</dt><dd>${p.version_count}</dd></div><div><dt>更新时间</dt><dd class="overview-date">${esc((p.version_updated || '').slice(0, 16) || '—')}</dd></div></dl><div class="overview-card-footer"><button type="button" class="overview-outline-button" data-overview-view="versions">管理版本</button></div></section>
      <section class="overview-panel">${header('归档管理', 'archive')}<dl class="overview-facts"><div><dt>已归档记录</dt><dd>${p.archive_count}</dd></div><div><dt>归档文件</dt><dd>${p.archive_files}</dd></div></dl><div class="overview-card-footer"><button type="button" class="overview-outline-button" data-overview-view="archive">进入归档</button></div></section>
      <section class="overview-panel">${header('差异报告', 'report')}<dl class="overview-facts"><div><dt>今日生成</dt><dd>${p.reports_today}</dd></div><div><dt>本月生成</dt><dd>${p.reports_month}</dd></div></dl><div class="overview-card-footer"><button type="button" class="overview-outline-button" data-overview-view="report">查看报告</button></div></section>
    </div>`;
  document.querySelectorAll('[data-overview-project]').forEach(button => {
    button.onclick = async () => {
      const project = projects[Number(button.dataset.overviewProject)];
      document.getElementById('projSel').value = project.name;
      await refreshProject();
      renderAgentOverview(projects);
      if (button.dataset.openView) navigateAgent(button.dataset.openView);
    };
  });
  document.querySelectorAll('[data-overview-view]').forEach(button => {
    button.disabled = index < 0;
    button.onclick = async () => {
      document.getElementById('projSel').value = current.name;
      if (PROJECT !== current.name) await refreshProject();
      navigateAgent(button.dataset.overviewView);
    };
  });
}
