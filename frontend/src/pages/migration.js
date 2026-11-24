import {feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const groupLabel = group => group ? `第 ${group} 群` : '噪声';
const ORDINAL = '一二三';

function setupForm(message, refresh) {
  const submit = button('设置看板并计算', {type:'submit', class:'primary', 'data-testid':'setup-migration'});
  return el('form', {class:'fields', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    await workspace.setupMigration({week_start:data.get('setup_week_start'), eps:data.get('eps'), min_samples:data.get('min_samples')});
    await refresh();
  })}, field({key:'setup_week_start', label:'第一周起始日（周一）', type:'date'}),
    el('div', {class:'form-grid'},
      field({key:'eps', label:'邻域半径 ε', type:'number', min:0.05, max:8, default:0.65}),
      field({key:'min_samples', label:'核心点最少邻居数', type:'int', min:2, max:500, default:5})),
    submit);
}

function importForm(message, refresh) {
  const submit = button('校验并导入修订', {type:'submit', class:'primary', 'data-testid':'import-week'});
  return el('form', {class:'fields', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const item = await workspace.importWeek({week_start:data.get('week_start'),
      revision:Number(data.get('revision')), csv_text:await file.text()});
    if (item.current) await refresh();
    else message.textContent = `已存档第 ${item.revision} 修订；${item.week_start} 当前最新为第 ${item.latest_revision} 修订，不触发重算。`;
  })}, field({key:'week_start', label:'统计周起始日（周一）', type:'date'}),
    field({key:'revision', label:'修订号', type:'int', min:1, default:1,
      help:'同一周按修订号取最新；乱序到达的旧修订只存档，不触发重算。'}),
    el('label', {class:'field', for:'source'}, el('span', {}, '周负荷 CSV'),
      el('input', {id:'source', type:'file', name:'source', accept:'.csv,text/csv', required:true})),
    submit);
}

function segmentPanel(title, segment) {
  const s = segment.summary;
  return panel(title,
    metrics([
      {label:'连续在册', value:s.continuing},
      {label:'留群', value:s.stayed, detail:'前后同一群'},
      {label:'转群', value:s.moved, detail:'换到另一群'},
      {label:'入群', value:s.entered, detail:'噪声落入群组'},
      {label:'出群', value:s.left, detail:'跌成噪声'},
      {label:'持续噪声', value:s.still_noise},
      {label:'转馆', value:s.transfers, detail:'同一教练换馆'},
      {label:'新进', value:s.newcomers, detail:'单列于下表'},
      {label:'缺席', value:s.absent, detail:'单列于下表'},
    ]),
    h(3, '前一周群组画像与流动'),
    table([
      {label:'群组', render:row=>groupLabel(row.group)},
      {key:'size', label:'教练人数'}, {key:'core', label:'核心'}, {key:'border', label:'边界'},
      {key:'median_hours', label:'带课中位（小时）'}, {key:'median_peak', label:'高峰中位（小时）'},
      {key:'median_visits', label:'签到中位（人次）'}, {key:'median_cover', label:'代课中位（班次）'},
      {key:'inflow', label:'流入'}, {key:'outflow', label:'流出'},
    ], segment.groups, {caption:'前一周群组画像与流动', empty:'前一周没有形成密度群组。'}),
    h(3, '连续在册教练去向'),
    table([
      {key:'code', label:'教练编号'},
      {label:'原群', render:row=>groupLabel(row.origin_group)},
      {key:'origin_kind', label:'原类型'},
      {label:'归属泳馆', render:row=>row.transfer ? `${row.pool_from} → ${row.pool_to}` : row.pool_to},
      {label:'转馆', render:row=>row.transfer ? '是' : '—'},
      {label:'去向', render:row=>groupLabel(row.dest_group)},
      {key:'dest_core_neighbors', label:'核心邻居'},
      {key:'dest_neighbors', label:'邻域邻居'},
      {label:'最近核心距离', render:row=>value(row.nearest)},
      {key:'category', label:'迁移类别'},
    ], segment.coaches, {caption:'连续在册教练去向', empty:'两周没有共同在册的教练。'}),
    h(3, '新进教练（单列）'),
    table([
      {key:'code', label:'教练编号'}, {key:'pool', label:'归属泳馆'},
      {label:'去向', render:row=>groupLabel(row.dest_group)},
      {key:'dest_core_neighbors', label:'核心邻居'}, {key:'dest_neighbors', label:'邻域邻居'},
      {label:'最近核心距离', render:row=>value(row.nearest)},
    ], segment.newcomers, {caption:'新进教练', empty:'本段没有新进教练。'}),
    h(3, '缺席教练（单列）'),
    table([
      {key:'code', label:'教练编号'}, {key:'pool', label:'原归属泳馆'},
      {label:'原群', render:row=>groupLabel(row.origin_group)}, {key:'origin_kind', label:'原类型'},
    ], segment.absent, {caption:'缺席教练', empty:'本段没有缺席教练。'}),
    el('details', {}, el('summary', {}, '前一周标准化尺度'),
      table([{key:'label', label:'指标'}, {key:'mean', label:'原始均值'}, {key:'sigma', label:'总体标准差'}],
        segment.scale, {caption:'前一周标准化尺度'})));
}

function reportView(report, historical) {
  const result = report.result;
  return el('section', {class:'analysis', 'data-testid':'migration-report'},
    el('div', {class:'result-head'},
      p(`版本 #${report.id} · 第 ${report.generation} 版 · ${date(report.finished_at)}`, 'eyeline'),
      h(2, result.headline),
      p(result.notes.join(' '), 'intro-copy'),
      historical && link('返回当前看板', '#migration', {class:'quiet'})),
    result.segments.map((segment, index) => segmentPanel(
      `第${ORDINAL[index]}段 ${segment.from_week}（修订 ${segment.from_revision}）→ ${segment.to_week}（修订 ${segment.to_revision}）`,
      segment)));
}

function basisDetails(basis) {
  const coach = basis.coach;
  const rows = [
    ['依据版本', `报告 #${basis.report_id} · 第 ${basis.generation} 版`],
    ['三周与修订', basis.weeks.map((week, index) => `${week}（修订 ${basis.revisions[index]}）`).join(' → ')],
    ['依据段', `${basis.segment.from_week}（修订 ${basis.segment.from_revision}）→ ${basis.segment.to_week}（修订 ${basis.segment.to_revision}）`],
    ['计算参数', `ε ${basis.params.eps} · 最少邻居 ${basis.params.min_samples}`],
  ];
  if (coach.origin_group === undefined) {
    rows.push(['新进', `${coach.code} · ${coach.pool}`], ['去向', groupLabel(coach.dest_group)]);
  } else {
    rows.push(['原群', `${groupLabel(coach.origin_group)} · ${coach.origin_kind} · ${coach.pool_from}`],
      ['去向', `${groupLabel(coach.dest_group)} · ${coach.pool_to}${coach.transfer ? ' · 转馆' : ''}`],
      ['迁移类别', coach.category]);
  }
  rows.push(['邻域', `核心邻居 ${coach.dest_core_neighbors} · 邻域邻居 ${coach.dest_neighbors} · 最近核心距离 ${value(coach.nearest)}`]);
  return el('dl', {class:'dictionary'}, rows.map(([term, text]) => el('div', {}, el('dt', {}, term), el('dd', {}, text))));
}

function suggestForm(spec, report, message, refresh) {
  const segment = report.result.segments[1];
  const coaches = [
    ...segment.coaches.map(row => [row.code, `${row.code} · ${row.category} · 去向${groupLabel(row.dest_group)}`]),
    ...segment.newcomers.map(row => [row.code, `${row.code} · 新进 · 去向${groupLabel(row.dest_group)}`]),
  ];
  if (!coaches.length) return null;
  const submit = button('发出补班建议', {type:'submit', class:'primary', 'data-testid':'issue-suggestion'});
  return el('form', {class:'fields', onSubmit:handler(submit, message, async event => {
    const data = new FormData(event.target);
    await workspace.suggest({coach_id:data.get('coach_id'), action:data.get('action'), note:data.get('note')});
    await refresh();
  })},
    el('label', {class:'field', for:'coach_id'}, el('span', {}, '教练（按第二段去向）'),
      el('select', {id:'coach_id', name:'coach_id'}, coaches.map(([code, label]) => el('option', {value:code}, label)))),
    el('label', {class:'field', for:'action'}, el('span', {}, '补班建议'),
      el('select', {id:'action', name:'action'}, spec.suggestion_actions.map(action => el('option', {value:action}, action)))),
    el('label', {class:'field', for:'note'}, el('span', {}, '说明'),
      el('textarea', {id:'note', name:'note', required:true, maxlength:500, rows:2})),
    submit);
}

function boardStatus(state) {
  if (!state.board) return p('尚未设置连续三周看板。', 'empty');
  const {board, latest_revisions} = state;
  return el('dl', {class:'dictionary'},
    board.weeks.map((week, index) => el('div', {}, el('dt', {}, `第${ORDINAL[index]}周`),
      el('dd', {}, `${week} · 修订 ${latest_revisions[week] ?? '暂无'}`))),
    el('div', {}, el('dt', {}, '计算参数'), el('dd', {}, `ε ${board.params.eps} · 最少邻居 ${board.params.min_samples}`)),
    el('div', {}, el('dt', {}, '当前版本'), el('dd', {}, state.current_report ? `报告 #${state.current_report.id}` : '尚未发布')));
}

function recomputeButton(state, message, refresh) {
  if (!state.board || !session.canWrite) return null;
  const trigger = button('按当前修订重算', {class:'quiet', 'data-testid':'recompute-migration'});
  trigger.addEventListener('click', handler(trigger, message, async () => {
    await workspace.recomputeMigration();
    await refresh();
  }));
  return el('div', {class:'actions'}, trigger);
}

export async function migrationPage(route, refresh) {
  const {spec} = workspace;
  const state = await workspace.migrationBoard();
  const [suggestions, reports] = await Promise.all([workspace.migrationSuggestions(), workspace.migrationReports()]);
  const message = feedback();
  const historical = route.id ? await workspace.migrationReport(route.id) : null;

  if (state.pending && !historical) {
    const original = location.hash;
    poll(() => workspace.migrationBoard(), next => !next.pending, () => location.hash === original)
      .then(done => { if (done && location.hash === original) refresh(); })
      .catch(() => {});
  }

  const statusNotes = [];
  if (state.pending) statusNotes.push(el('p', {role:'status'},
    `第 ${state.pending.generation} 版正在重算（修订 ${state.pending.revisions.join(' / ')}），两段算齐后一次性切换，页面继续显示当前版本。`));
  if (state.last_failed) statusNotes.push(el('p', {class:'feedback', role:'alert'},
    `第 ${state.last_failed.generation} 版重算失败：${state.last_failed.error}。页面继续显示上一可用版本。`));

  const main = [];
  if (historical) {
    main.push(historical.result
      ? reportView(historical, true)
      : panel(`版本 #${historical.id}`, pill(STATUS[historical.status], historical.status),
        p(historical.error || '该版本没有计算结果。', 'feedback'), link('返回当前看板', '#migration', {class:'quiet'})));
  } else if (state.current_report) {
    main.push(...statusNotes, reportView(state.current_report, false),
      panel('已发出的补班建议',
        session.canWrite && suggestForm(spec, state.current_report, message, refresh),
        table([
          {key:'coach_id', label:'教练'}, {key:'action', label:'补班建议'}, {key:'target_week', label:'目标周'},
          {key:'author', label:'操作人'}, {label:'时间', render:row=>date(row.created_at)},
          {label:'当时的迁移依据', render:row=>el('details', {},
            el('summary', {}, `报告 #${row.report_id} · 第 ${row.basis.generation} 版`), basisDetails(row.basis))},
        ], suggestions, {caption:'已发出的补班建议', empty:'还没有发出补班建议。'})));
  } else if (state.board) {
    main.push(...statusNotes, panel('迁移结果', state.pending
      ? p('首个版本正在计算，完成后自动展示。', 'empty')
      : empty('还没有可用版本，请先导入三周的数据修订。')));
  } else {
    main.push(panel(spec.pages[6], session.canWrite
      ? el('div', {}, p('选择连续三周的第一周（周一）。每段用前一周的标准化尺度和核心群判断后一周去向，两段算齐后一次性切换。'), setupForm(message, refresh))
      : empty('迁移看板尚未设置，请等待分析员配置。')));
  }

  const sidebar = [panel('看板状态', boardStatus(state), recomputeButton(state, message, refresh))];
  if (session.canWrite) {
    sidebar.push(panel('导入周数据修订', importForm(message, refresh)));
    if (state.board) sidebar.push(panel('重新设置看板',
      el('details', {}, el('summary', {}, '更换连续三周与参数'), setupForm(message, refresh))));
  }
  sidebar.push(panel('最近导入的周修订', table([
    {key:'week_start', label:'统计周'}, {key:'revision', label:'修订'}, {key:'row_count', label:'行数'},
    {label:'导入时间', render:row=>date(row.created_at)},
  ], state.recent_snapshots, {caption:'最近导入的周修订', empty:'还没有导入周数据修订。'})));
  sidebar.push(panel('版本历史', table([
    {label:'版本', render:row=>link(`#${row.id} · 第 ${row.generation} 版`, `#migration/${row.id}`)},
    {label:'状态', render:row=>pill(STATUS[row.status], row.status)},
    {label:'修订', render:row=>row.revisions.join(' / ')},
    {label:'时间', render:row=>date(row.created_at)},
  ], reports, {caption:'迁移版本历史', empty:'还没有迁移版本。'})), message);

  return el('div', {class:'two-col'}, el('div', {class:'wide-panel'}, main), el('aside', {class:'sidebar-panel'}, sidebar));
}
