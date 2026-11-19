<template>
  <section class="page-panel schemes" v-loading="busy">
    <div class="page-header">
      <div><h1>考核方案管理</h1><p>导入HR正式方案，按部门、岗位和周期发布；历史考核保留原版本。</p></div>
      <div v-if="options.can_manage" class="actions">
        <el-button @click="run(downloadSchemeTemplate)">下载Excel模板</el-button>
        <el-button @click="newDraft">新建方案</el-button>
        <el-button type="primary" @click="importVisible = true">批量导入方案</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon @close="error = ''" />
    <el-tabs v-model="tab">
      <el-tab-pane label="方案与版本" name="schemes">
        <div class="actions filters">
          <el-select v-model="departmentFilter" clearable placeholder="全部部门" filterable style="width:220px"><el-option v-for="d in options.departments" :key="d.id" :label="d.name" :value="d.id" /></el-select>
          <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width:150px"><el-option v-for="(label, key) in statuses" :key="key" :label="label" :value="key" /></el-select>
          <el-input v-model="search" placeholder="方案名称 / 编码 / 岗位" clearable style="width:250px" />
          <el-button @click="run(load)">刷新</el-button>
        </div>
        <el-table :data="filtered" stripe empty-text="暂无方案，请由HR导入或新建">
          <el-table-column prop="payload.name" label="方案名称" min-width="190" />
          <el-table-column label="部门 / 岗位" min-width="180"><template #default="{ row }">{{ departmentName(row.payload.binding.department_id) }} / {{ row.payload.binding.position || '部门通用' }}</template></el-table-column>
          <el-table-column label="周期" width="80"><template #default="{ row }">{{ periods[row.payload.binding.period_type] }}</template></el-table-column>
          <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version_no }}</template></el-table-column>
          <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'published' ? 'success' : 'info'">{{ statuses[row.status] }}</el-tag></template></el-table-column>
          <el-table-column label="生效范围" min-width="200"><template #default="{ row }">{{ row.payload.binding.effective_from }} ~ {{ row.payload.binding.effective_to }}</template></el-table-column>
          <el-table-column label="操作" min-width="240"><template #default="{ row }">
            <el-button link type="primary" @click="open(row)">{{ options.can_manage && row.status === 'draft' ? '编辑' : '查看' }}</el-button>
            <el-button v-if="options.can_manage" link @click="run(() => copyVersion(row))">复制版本</el-button>
            <el-button v-if="options.can_manage && row.status === 'draft'" link type="success" @click="run(() => action(row, 'publish'))">发布</el-button>
            <el-button v-if="options.can_manage && row.status === 'published'" link type="danger" @click="run(() => action(row, 'retire'))">停用</el-button>
          </template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="配置覆盖与发起考核" name="coverage">
        <div class="actions filters">
          <el-select v-model="periodType" style="width:130px"><el-option v-for="(label, key) in periods" :key="key" :label="label" :value="key" /></el-select>
          <el-input v-model="period" placeholder="202701 / 2027Q1 / 2027" style="width:220px" />
          <el-button @click="run(loadCoverage)">检查配置覆盖</el-button>
          <el-switch v-model="onlyMissing" active-text="只看缺失" />
          <el-button v-if="options.can_manage" type="primary" :disabled="!selectedEmployees.length" @click="startVisible = true">为选中员工发起考核</el-button>
        </div>
        <el-table :data="coverageRows" @selection-change="(rows: Coverage['employees']) => selectedEmployees = rows.map(r => r.employee_id)">
          <el-table-column v-if="options.can_manage" type="selection" width="45" :selectable="(r: Coverage['employees'][number]) => r.status === 'configured'" />
          <el-table-column prop="name" label="员工" /><el-table-column label="部门"><template #default="{ row }">{{ departmentName(row.department_id) }}</template></el-table-column>
          <el-table-column prop="position" label="岗位" /><el-table-column prop="scheme_name" label="适用方案" />
          <el-table-column label="检查结果" min-width="220"><template #default="{ row }"><span :class="{ missing: row.status !== 'configured' }">{{ row.message || '已配置' }}</span></template></el-table-column>
        </el-table>
        <p v-if="coverage.empty_departments.length">暂无在职员工的部门：{{ coverage.empty_departments.map(d => d.name).join('、') }}。可提前在方案中配置绑定。</p>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="importVisible"
      class="scheme-import-dialog"
      title="考核方案"
      modal-class="scheme-import-overlay"
      width="min(1100px, calc(100% - 48px))"
      append-to-body
      align-center
    >
      <el-alert
        class="scheme-import-notice"
        title="支持 Excel（.xlsx）和 Word（.docx）表格，自动识别指标、权重和评分说明等常见表头。请保留等级规则；缺少的适用范围可在下方补充。导入生成草稿，核对并发布后生效。"
        type="info"
        :closable="false"
      />
      <div class="scheme-import-file-row">
        <label class="scheme-import-file-picker">
          <span>{{ batch?.file_name || '选择 Word / Excel 文件' }}</span>
          <input type="file" accept=".xlsx,.docx" aria-label="选择考核方案Word或Excel文件" :disabled="busy" @change="chooseFile" />
        </label>
        <span v-if="batch" class="scheme-import-file-status">{{ batch.file_name }} · {{ batch.status }}</span>
      </div>
      <el-collapse v-if="batch && batch.status !== 'committed'" class="scheme-import-mapping">
        <el-collapse-item title="补充方案适用范围（文件未填写时使用）" name="defaults">
          <div class="form-grid">
            <label>方案名称<el-input v-model="importDefaults.scheme_name" placeholder="默认使用文件名" /></label>
            <label>部门<el-select v-model="importDefaults.department_id" clearable filterable><el-option v-for="d in options.departments" :key="d.id" :label="d.name" :value="d.id" /></el-select></label>
            <label>岗位<el-input v-model="importDefaults.position" placeholder="留空为部门通用" /></label>
            <label>周期<el-select v-model="importDefaults.period_type" clearable><el-option v-for="(label, key) in periods" :key="key" :label="label" :value="key" /></el-select></label>
            <label>生效日期<el-date-picker v-model="importDefaults.effective_from" value-format="YYYY-MM-DD" /></label>
            <label>结束日期<el-date-picker v-model="importDefaults.effective_to" value-format="YYYY-MM-DD" /></label>
          </div>
          <el-button @click="run(applyMapping)">保存补充信息并重新校验</el-button>
        </el-collapse-item>
        <el-collapse-item title="调整列映射（填写Word或Excel表格中的实际表头）">
          <div v-for="(columns, sheet) in options.columns" :key="sheet"><h3>{{ sheet }}</h3><div class="mapping-grid">
            <label v-for="(label, key) in columns" :key="key">{{ label }}<el-input v-model="mapping[String(sheet)][String(key)]" :placeholder="label" /></label>
          </div></div>
          <el-button @click="run(applyMapping)">保存映射并重新校验</el-button>
        </el-collapse-item>
      </el-collapse>
      <el-alert v-for="warning in batch?.preview.warnings || []" :key="warning" :title="warning" type="warning" :closable="false" />
      <el-table
        v-if="batch?.preview.errors?.length"
        class="scheme-import-errors"
        :data="batch.preview.errors"
        max-height="220"
        table-layout="fixed"
      >
        <el-table-column prop="sheet" label="表格" width="110" />
        <el-table-column prop="row" label="行号" width="70" />
        <el-table-column prop="field" label="字段 / 方案" width="220" />
        <el-table-column prop="message" label="错误原因" min-width="240" />
      </el-table>
      <div v-for="p in batch?.preview.schemes || []" :key="p.code" class="preview-card">
        <h3>{{ p.name }} · {{ p.code }}</h3>
        <p>{{ departmentName(p.binding.department_id) }} / {{ p.binding.position || '未指定岗位' }} · {{ p.binding.effective_from || '生效日期待补充' }} ~ {{ p.binding.effective_to || '结束日期待补充' }}</p>
        <template v-if="p.document">
          <el-alert title="以下分别展示指标、等级、加减分与完整原文。原文已随草稿保存；保留原文不等于所有条款已转为自动计分规则。部门、岗位和日期请在上方补充或在草稿中设置。" type="info" :closable="false" />
          <p>识别到 {{ p.metrics.length }} 个指标、{{ p.document.grade_rules.length }} 档等级、{{ p.document.adjustment_rules.length }} 条加减分；基础满分 {{ p.document.base_score }} 分。</p>
          <h3>需核对事项</h3><p v-for="issue in p.document.issues" :key="issue" class="missing">{{ issue }}</p>
          <h3>考核维度与权重、适用范围及岗位职责（原文）</h3>
          <div v-for="(block, i) in documentIntroduction(p)" :key="i" class="source-block">
            <p v-if="block.text">{{ block.text }}</p>
            <table v-if="block.rows" class="source-table"><tbody><tr v-for="(row, j) in block.rows" :key="j"><td v-for="(cell, k) in row" :key="k">{{ cell }}</td></tr></tbody></table>
          </div>
        </template>
        <h3>指标与评分标准</h3>
        <el-table :data="p.metrics">
          <el-table-column v-if="p.document" prop="dimension" label="所属维度" min-width="160" />
          <el-table-column prop="name" label="指标" min-width="160" />
          <el-table-column :prop="p.scoring_mode === 'direct_points' ? 'max_score' : 'weight'" :label="p.scoring_mode === 'direct_points' ? '满分' : '权重(%)'" width="80" />
          <el-table-column label="评分方式" width="130"><template #default="{ row }">{{ row.scoring_method === 'manager_review' ? '主管依据原文评价' : '达成率区间计算' }}</template></el-table-column>
          <el-table-column prop="scoring_description" label="评分说明（原文）" min-width="300" />
        </el-table>
        <template v-if="p.document">
          <h3>加减分条款（原文）</h3>
          <table class="source-table"><tbody><tr v-for="(rule, i) in p.document.adjustment_rules" :key="i"><td v-for="(cell, j) in rule.cells" :key="j">{{ cell }}</td></tr></tbody></table>
          <h3>考核等级与结果应用（原文）</h3>
          <el-table :data="p.document.grade_rules"><el-table-column prop="level" label="等级" width="120" /><el-table-column prop="condition" label="总分区间" width="150" /><el-table-column prop="description" label="绩效系数与结果应用" min-width="300" /></el-table>
          <h3>完整文档（含考核流程、附则、保护期及附录）</h3>
          <div v-for="(block, i) in p.document.blocks" :key="i" class="source-block">
            <p v-if="block.text">{{ block.text }}</p>
            <table v-if="block.rows" class="source-table"><tbody><tr v-for="(row, j) in block.rows" :key="j"><td v-for="(cell, k) in row" :key="k">{{ cell }}</td></tr></tbody></table>
          </div>
        </template>
      </div>
      <template #footer>
        <div class="scheme-import-footer">
          <el-button @click="importVisible = false">关闭</el-button>
          <el-button type="primary" :disabled="busy || !batch || batch.status !== 'validated'" @click="run(commitImport)">
            确认导入为草稿
          </el-button>
        </div>
      </template>
    </el-dialog>

    <el-drawer
      v-model="editorVisible"
      class="scheme-editor-drawer"
      :title="editing ? `${draft.name} · v${editing.version_no}` : '新建考核方案'"
      size="calc(100% - 268px)"
      append-to-body
    >
      <el-alert v-if="!editable" title="此版本只读；调整方案请复制为新草稿。" type="info" :closable="false" />
      <el-tabs>
        <el-tab-pane label="方案配置">
          <el-form label-position="top" :disabled="!editable">
            <div class="form-grid"><el-form-item label="方案编码"><el-input v-model="draft.code" :disabled="!!editing" /></el-form-item><el-form-item label="方案名称"><el-input v-model="draft.name" /></el-form-item>
              <el-form-item label="部门"><el-select v-model="draft.binding.department_id" filterable><el-option v-for="d in options.departments" :key="d.id" :label="`${d.name} (${d.id})`" :value="d.id" /></el-select></el-form-item>
              <el-form-item label="岗位（留空为部门通用）"><el-select v-model="draft.binding.position" filterable allow-create default-first-option clearable><el-option v-for="p in options.positions" :key="p" :label="p" :value="p" /></el-select></el-form-item>
              <el-form-item label="周期"><el-select v-model="draft.binding.period_type"><el-option v-for="(label, key) in periods" :key="key" :label="label" :value="key" /></el-select></el-form-item>
              <el-form-item label="默认展示方式"><el-select v-model="draft.output_mode"><el-option label="量化表格" value="quantitative" /><el-option label="工作成果文字" value="narrative" /></el-select></el-form-item>
              <el-form-item label="生效日期"><el-date-picker v-model="draft.binding.effective_from" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="结束日期"><el-date-picker v-model="draft.binding.effective_to" value-format="YYYY-MM-DD" /></el-form-item>
            </div>
            <div v-if="draft.document" ref="reviewSection" class="document-review-section">
              <el-alert v-if="!draft.document.review_confirmed || !draft.document.review_note.trim()" title="发布前还需完成：填写下方核对说明、勾选核对确认，再点击底部“保存并发布”。" type="error" :closable="false" />
              <el-alert title="业务文档方案：按各指标满分直接计分，等级由主管依据原文确认。请核对以下原文差异后再发布。" type="warning" :closable="false" />
              <p v-for="issue in draft.document.issues" :key="issue" class="missing">{{ issue }}</p>
              <h3>原文等级规则</h3>
              <p v-for="g in draft.document.grade_rules" :key="g.level"><strong>{{ g.level }}：{{ g.condition }}</strong> · {{ g.description }}</p>
              <h3>原文加减分规则</h3><p v-for="(r, i) in draft.document.adjustment_rules" :key="i">{{ r.cells.join(' · ') }}</p>
              <div class="actions"><label>加减分下限<el-input-number v-model="draft.document.adjustment_min" :max="0" /></label><label>加减分上限<el-input-number v-model="draft.document.adjustment_max" :min="0" /></label></div>
              <el-form-item label="核对说明（说明正文与附录差异的执行依据、等级边界处理及加减分封顶）"><el-input v-model="draft.document.review_note" type="textarea" :rows="3" placeholder="请填写公司确认的执行依据；例如采用正文或附录分值、如何处理等级边界、加减分范围。此说明会随方案保存。" /></el-form-item>
              <el-checkbox v-model="draft.document.review_confirmed">已核对指标分值、原文差异、加减分和等级，并确认按本方案执行</el-checkbox>
              <el-collapse><el-collapse-item title="查看完整原文"><div v-for="(block, i) in draft.document.blocks" :key="i"><p v-if="block.text">{{ block.text }}</p><template v-else><p v-for="(row, j) in block.rows" :key="j">{{ row.join(' | ') }}</p></template></div></el-collapse-item></el-collapse>
            </div>
            <h3>{{ draft.scoring_mode === 'direct_points' ? '指标与满分 · 合计 ' + draft.metrics.reduce((n, m) => n + (m.max_score || 0), 0) + '分' : '指标与权重 · 合计 ' + weightTotal + '%' }}</h3>
            <div v-for="(m, i) in draft.metrics" :key="i" class="metric-card">
              <div class="form-grid"><el-form-item label="指标编码"><el-input v-model="m.code" /></el-form-item><el-form-item label="指标名称"><el-input v-model="m.name" /></el-form-item><el-form-item label="指标类型"><el-select v-model="m.type"><el-option v-for="t in ['number', 'percentage', 'currency', 'count', 'narrative', 'rating']" :key="t" :label="t" :value="t" /></el-select></el-form-item><el-form-item v-if="draft.scoring_mode === 'direct_points'" label="指标满分"><el-input-number v-model="m.max_score" :min="0.01" /></el-form-item><el-form-item v-else label="权重(%)"><el-input-number v-model="m.weight" :min="0" :max="100" /></el-form-item><el-form-item label="单位"><el-input v-model="m.unit" /></el-form-item><el-form-item label="默认目标（可留空）"><el-input-number v-model="m.target" /></el-form-item><el-form-item label="评分方式"><el-select v-model="m.scoring_method"><el-option label="主管按说明评价" value="manager_review" /><el-option label="按达成率区间计算" value="ratio" /></el-select></el-form-item><el-form-item label="方向"><el-select v-model="m.direction"><el-option label="越高越好" value="higher_is_better" /><el-option label="越低越好" value="lower_is_better" /></el-select></el-form-item></div>
              <el-form-item label="正式评分说明"><el-input v-model="m.scoring_description" type="textarea" :rows="3" /></el-form-item><el-checkbox v-model="m.evidence_required">成果证据必填</el-checkbox>
              <div v-if="m.scoring_method === 'ratio'"><p>达成率区间使用小数（1 = 100%），从0连续覆盖到无上限。</p><div v-for="(r, j) in m.rules" :key="j" class="actions rule-row"><el-input v-model="r.level" placeholder="等级编码" /><el-input-number v-model="r.min_ratio" placeholder="下限" /><el-input-number v-model="r.max_ratio" placeholder="上限，留空无上限" /><el-input-number v-model="r.score" :min="0" :max="100" /><el-button @click="m.rules.splice(j, 1)">删除区间</el-button></div><el-button @click="m.rules.push({level:'', min_ratio:0, max_ratio:null, score:0})">新增区间</el-button></div>
              <el-button v-if="editable" type="danger" link @click="draft.metrics.splice(i, 1)">删除指标</el-button>
            </div>
            <el-button v-if="editable" @click="addMetric">新增指标</el-button>
            <template v-if="!draft.document"><h3>总分等级规则</h3><p>范围从0连续覆盖到100，最后一区间包含100。</p>
            <div v-for="(g, i) in draft.grades" :key="i" class="actions rule-row"><el-input v-model="g.level" placeholder="等级编码" /><el-input-number v-model="g.min_score" :min="0" :max="100" /><el-input-number v-model="g.max_score" :min="0" :max="100" /><el-input v-model="g.description" placeholder="等级说明" /><el-button @click="draft.grades.splice(i, 1)">删除</el-button></div><el-button v-if="editable" @click="draft.grades.push({level:'', min_score:0, max_score:100, description:''})">新增等级</el-button>
            </template><h3>填报字段</h3><p>留空时按指标生成成果字段；主管评价字段不展示给员工。</p>
            <div v-for="(f, i) in draft.fields" :key="i" class="actions rule-row"><el-select v-model="f.metric_code" placeholder="指标"><el-option v-for="m in draft.metrics" :key="m.code" :label="m.name" :value="m.code" /></el-select><el-input v-model="f.key" placeholder="字段编码" /><el-input v-model="f.label" placeholder="字段名称" /><el-select v-model="f.stage"><el-option v-for="s in ['plan','result','self_review','manager_review']" :key="s" :label="s" :value="s" /></el-select><el-select v-model="f.type"><el-option v-for="t in ['text','textarea','number','attachment']" :key="t" :label="t" :value="t" /></el-select><el-checkbox v-model="f.required">必填</el-checkbox><el-button @click="draft.fields.splice(i,1)">删除</el-button></div><el-button v-if="editable" @click="draft.fields.push({metric_code:'',key:'',label:'',stage:'result',type:'textarea',required:true})">新增字段</el-button>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="员工表单预览"><h3>{{ draft.name }}</h3><div v-for="m in draft.metrics" :key="m.code" class="metric-card"><strong>{{ m.name }} · {{ draft.scoring_mode === 'direct_points' ? `${m.max_score}分` : `${m.weight}%` }}</strong><p>{{ m.scoring_description }}</p><p v-if="m.scoring_method === 'ratio'">目标：{{ m.target ?? '周期内填写' }} {{ m.unit }}</p><el-input :type="m.type === 'narrative' ? 'textarea' : 'text'" disabled :placeholder="m.type === 'narrative' ? '填写完成情况与交付成果' : `填写实际完成值（${m.unit || ''}）`" /></div></el-tab-pane>
        <el-tab-pane label="版本对比"><el-select v-model="compareId" clearable placeholder="选择同方案另一版本"><el-option v-for="v in versions.filter(v => v.scheme_id === editing?.scheme_id && v.id !== editing?.id)" :key="v.id" :label="`v${v.version_no}`" :value="v.id" /></el-select><el-table :data="differences"><el-table-column prop="field" label="内容" /><el-table-column prop="before" label="对比版本" /><el-table-column prop="after" label="当前内容" /></el-table></el-tab-pane>
        <el-tab-pane label="操作记录"><el-table :data="auditRows"><el-table-column prop="action" label="操作" /><el-table-column prop="actor_id" label="操作人" /><el-table-column prop="created_at" label="时间" /></el-table></el-tab-pane>
      </el-tabs>
      <template #footer><el-button @click="editorVisible = false">关闭</el-button><el-button v-if="editable" @click="run(save)">保存草稿</el-button><el-button v-if="editable" type="primary" @click="run(saveAndPublish)">保存并发布</el-button></template>
    </el-drawer>
    <el-dialog v-model="startVisible" title="按匹配方案发起考核"><el-alert :title="`已选择${selectedEmployees.length}名员工，系统按方案版本分组冻结快照。`" type="info" :closable="false" /><el-form label-position="top"><el-form-item label="考核标题"><el-input v-model="startTitle" /></el-form-item><el-form-item label="自评截止时间"><el-date-picker v-model="selfDeadline" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item><el-form-item label="主管评分截止时间"><el-date-picker v-model="managerDeadline" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item></el-form><template #footer><el-button type="primary" @click="run(start)">确认发起</el-button></template></el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { currentPeriod } from '@/utils/period'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listSchemes, schemeOptions, schemeCoverage, uploadScheme, mapScheme, validateScheme, commitScheme, createScheme, saveScheme, schemeAction, schemeAudit, downloadSchemeTemplate, startSchemeTasks, type SchemePayload, type SchemeVersion, type SchemeOptions, type ImportBatch, type Coverage } from '@/api/schemes'
const busy = ref(false), error = ref(''), tab = ref('schemes')
const options = ref<SchemeOptions>({can_manage:false, departments:[], positions:[], columns:{}})
const versions = ref<SchemeVersion[]>([]), departmentFilter = ref(''), statusFilter = ref(''), search = ref('')
const statuses: Record<string,string> = {draft:'草稿',published:'已发布',retired:'已停用'}
const periods: Record<string,string> = {monthly:'月度',quarterly:'季度',annual:'年度'}
const filtered = computed(() => versions.value.filter(v => (!departmentFilter.value || v.payload.binding.department_id === departmentFilter.value) && (!statusFilter.value || v.status === statusFilter.value) && `${v.payload.name} ${v.payload.code} ${v.payload.binding.position}`.includes(search.value)))
const period = ref(currentPeriod()), periodType = ref('monthly'), onlyMissing = ref(false)
const coverage = ref<Coverage>({employees:[],empty_departments:[]}), selectedEmployees = ref<string[]>([])
const coverageRows = computed(() => coverage.value.employees.filter(e => !onlyMissing.value || e.status !== 'configured'))
const importVisible = ref(false), batch = ref<ImportBatch | null>(null), mapping = ref<ImportBatch['mapping']>({})
const importDefaults = ref<Record<string, string>>({})
const reviewSection = ref<HTMLElement | null>(null)
const editorVisible = ref(false), editing = ref<SchemeVersion | null>(null), compareId = ref('')
function empty(): SchemePayload { return {code:'',name:'',output_mode:'quantitative',binding:{department_id:'',position:'',period_type:'monthly',effective_from:'',effective_to:''},metrics:[],grades:[],fields:[]} }
const draft = ref<SchemePayload>(empty()), auditRows = ref<{action:string;actor_id:string;created_at:string}[]>([])
const editable = computed(() => options.value.can_manage && (!editing.value || editing.value.status === 'draft'))
const weightTotal = computed(() => draft.value.metrics.reduce((n,m) => n + Number(m.weight || 0), 0))
const differences = computed(() => { const other = versions.value.find(v => v.id === compareId.value); if (!other) return []; return (Object.keys(draft.value) as (keyof SchemePayload)[]).filter(k => JSON.stringify(other.payload[k]) !== JSON.stringify(draft.value[k])).map(k => ({field:k,before:JSON.stringify(other.payload[k]),after:JSON.stringify(draft.value[k])})) })
const startVisible = ref(false), startTitle = ref(''), selfDeadline = ref(''), managerDeadline = ref('')
function documentIntroduction(p: SchemePayload) {
  const blocks = p.document?.blocks || []
  const firstMetricSource = p.metrics[0]?.source?.replace(/第\d+行$/, '')
  const end = blocks.findIndex(block => block.source === firstMetricSource)
  return end >= 0 ? blocks.slice(0, end) : []
}
function departmentName(id?: string) { return options.value.departments.find(d => d.id === id)?.name || id || '未设置' }
async function run(fn: () => Promise<unknown>) { busy.value = true; error.value = ''; try { await fn() } catch (e) { if (e !== 'cancel' && e !== 'close') { error.value = e instanceof Error ? e.message : String(e); ElMessage.error(error.value) } } finally { busy.value = false } }
async function load() { const [o,v] = await Promise.all([schemeOptions(),listSchemes()]); options.value=o; versions.value=v }
async function loadCoverage() { coverage.value=await schemeCoverage(period.value,periodType.value); selectedEmployees.value=[] }
async function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await run(async () => {
    batch.value = null
    importDefaults.value = {}
    batch.value = await uploadScheme(file)
    importDefaults.value = { ...batch.value.mapping._defaults }
    mapping.value = Object.fromEntries(Object.keys(options.value.columns).map(s => [s, { ...batch.value?.mapping[s] }]))
  })
  input.value = ''
}
async function applyMapping() {
  if (!batch.value) return
  const columns = Object.fromEntries(Object.entries(mapping.value).map(([sheet, fields]) => [sheet, Object.fromEntries(Object.entries(fields).filter(([, value]) => value?.trim()))]))
  batch.value = await mapScheme(batch.value, { ...columns, _defaults: importDefaults.value })
  batch.value = await validateScheme(batch.value.id)
}
async function commitImport() {
  if (!batch.value) return
  const imported = await commitScheme(batch.value)
  batch.value.status = 'committed'
  ElMessage.success('已导入为草稿，请核对完整原文、适用范围和规则后发布')
  await load()
  if (imported[0]) { importVisible.value = false; open(imported[0]) }
}
function newDraft() { editing.value=null; draft.value=empty(); auditRows.value=[]; compareId.value=''; editorVisible.value=true }
function open(v: SchemeVersion) { editing.value=v; draft.value=JSON.parse(JSON.stringify(v.payload)); compareId.value=''; auditRows.value=[]; editorVisible.value=true; void run(async () => { auditRows.value=await schemeAudit(v.id) }) }
function addMetric() { draft.value.metrics.push({code:'',name:'',type:'narrative',weight:0,unit:'',target:null,scoring_method:'manager_review',direction:'higher_is_better',scoring_description:'',evidence_required:false,rules:[]}) }
async function save() { if (!draft.value.fields.length) draft.value.fields=draft.value.metrics.map((m,i) => ({metric_code:m.code,key:`metric_${i}_result`,label:m.name,stage:'result',type:m.type === 'narrative' ? 'textarea' : 'number',required:true})); const v=editing.value ? await saveScheme(editing.value,draft.value) : await createScheme(draft.value); editing.value=v; ElMessage.success('草稿已保存'); await load() }
async function copyVersion(v: SchemeVersion) { const next=await schemeAction(v.id,'copy'); await load(); open(next) }
function reviewMissing(p: SchemePayload) {
  return !!p.document && (!p.document.review_confirmed || !p.document.review_note.trim())
}
async function focusReview() {
  await nextTick()
  reviewSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  ElMessage.warning('请填写核对说明并勾选核对确认，再保存并发布')
}
async function saveAndPublish() {
  if (reviewMissing(draft.value)) { await focusReview(); return }
  await ElMessageBox.confirm('将保存并发布当前方案，发布后内容锁定。请确认评分规则、适用范围和生效时间准确。', '保存并发布')
  await save()
  if (!editing.value) return
  await schemeAction(editing.value.id, 'publish')
  editorVisible.value = false
  ElMessage.success('方案已发布')
  await load()
}
async function action(v: SchemeVersion, a: string) {
  if (a === 'publish' && reviewMissing(v.payload)) {
    open(v)
    await focusReview()
    return
  }
  await ElMessageBox.confirm(a === 'publish' ? '发布后内容锁定。请确认指标、规则、适用范围和生效时间准确。' : '停用后不再匹配新任务，已创建任务保留原快照。', a === 'publish' ? '发布方案' : '停用方案')
  await schemeAction(v.id, a)
  ElMessage.success('操作成功')
  await load()
}
async function start() { const tasks=await startSchemeTasks({title:startTitle.value,period:period.value,period_type:periodType.value,employee_ids:selectedEmployees.value,self_review_deadline:selfDeadline.value,manager_review_deadline:managerDeadline.value}); startVisible.value=false; ElMessage.success(`已创建${tasks.length}个考核任务并冻结方案`); await loadCoverage() }
onMounted(() => run(load))
</script>

<style scoped>
.source-block p{white-space:pre-wrap}.source-table{width:100%;border-collapse:collapse;margin:12px 0;table-layout:fixed}.source-table td{border:1px solid #dce4df;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere;vertical-align:top}.source-table tr:first-child{background:#edf2ee}.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;min-width:0}.page-header{display:flex;justify-content:space-between;gap:20px;align-items:center;min-width:0}.page-header>div{min-width:0}.filters{margin:20px 0}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr));gap:0 18px}.metric-card,.preview-card{padding:20px;border:1px solid #dce4df;border-radius:10px;margin:16px 0;background:#fafcfb}.mapping-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:12px;margin-bottom:20px}.rule-row{margin:12px 0;flex-wrap:nowrap}.rule-row>.el-input,.rule-row>.el-select{min-width:110px}.missing{color:#b44831}p{color:#61736b;line-height:1.6}h3{margin-top:24px}@media(max-width:800px){.page-header{display:block}.page-header .actions{margin-top:14px}.rule-row{flex-wrap:wrap}.rule-row>.el-input,.rule-row>.el-select{flex:1 1 140px;min-width:0}.metric-card,.preview-card{padding:14px}.el-table{max-width:100%}}
</style>

<style>
.scheme-editor-drawer .el-drawer__body {
  min-height: 0;
  overflow: auto;
}
.scheme-editor-drawer .el-drawer__header,
.scheme-editor-drawer .el-drawer__footer {
  flex-shrink: 0;
}
@media (max-width: 1180px) {
  .scheme-editor-drawer.el-drawer { width: 100vw !important; }
}
/* 遮罩覆盖页面，弹窗在侧边栏右侧的工作区内居中。 */
.scheme-import-overlay .el-overlay-dialog {
  left: 268px;
  padding-bottom: 20vh;
  box-sizing: border-box;
}
/* 1180px 以下侧边栏收起为抽屉，浮层回到整屏居中 */
@media (max-width: 1180px) {
  .scheme-import-overlay .el-overlay-dialog {
    left: 0;
  }
}
.scheme-import-dialog.el-dialog {
  --el-dialog-padding-primary: 0;
  display: flex;
  flex-direction: column;
  max-width: 1600px;
  max-height: calc(80dvh - 32px);
  padding: 0;
  overflow: hidden;
  border-radius: 13px;
  background: #fff;
}
.scheme-import-dialog .el-dialog__header {
  margin: 0;
  padding: 32px 40px 24px;
  border-bottom: 1px solid #ebeef0;
}
.scheme-import-dialog .el-dialog__title {
  color: #263c34;
  font-size: 18px;
  font-weight: 700;
  line-height: 26px;
}
.scheme-import-dialog .el-dialog__headerbtn {
  top: 8px;
  right: 8px;
  width: 32px;
  height: 32px;
}
.scheme-import-dialog .el-dialog__header,
.scheme-import-dialog .el-dialog__footer {
  flex-shrink: 0;
}
.scheme-import-dialog .el-dialog__body {
  min-height: 0;
  overflow: auto;
  padding: 20px 40px;
  color: #606266;
  font-size: 14px;
}
.scheme-import-dialog .scheme-import-notice {
  padding: 14px 16px;
  border-radius: 8px;
  background: #f4f4f5;
  color: #909399;
}
.scheme-import-dialog .scheme-import-notice .el-alert__title {
  font-size: 14px;
  font-weight: 400;
  line-height: 22px;
}
.scheme-import-file-row {
  display: flex;
  align-items: center;
  gap: 32px;
  min-height: 64px;
  padding: 12px 0;
  box-sizing: border-box;
  font-size: 14px;
  line-height: 22px;
}
.scheme-import-file-picker {
  position: relative;
  max-width: 100%;
  overflow: hidden;
  border-radius: 4px;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.scheme-import-file-picker input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}
.scheme-import-file-picker:focus-within {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}
.scheme-import-file-status {
  color: #73767a;
  overflow-wrap: anywhere;
}
.scheme-import-dialog .scheme-import-mapping {
  border-color: #ebeef0;
}
.scheme-import-dialog .scheme-import-mapping .el-collapse-item__header {
  height: 46px;
  color: #606266;
  font-size: 14px;
  font-weight: 400;
}
.scheme-import-dialog .scheme-import-errors {
  width: 100%;
  min-width: 0;
  --el-table-border-color: #dcdfe6;
  --el-table-header-bg-color: #edf2ee;
  --el-table-header-text-color: #385b50;
  --el-table-text-color: #61736b;
}
.scheme-import-dialog .scheme-import-errors th.el-table__cell {
  height: 46px;
  padding: 10px 0;
  background: #edf2ee;
  color: #385b50;
  font-size: 14px;
  font-weight: 600;
}
.scheme-import-dialog .scheme-import-errors td.el-table__cell {
  height: 52px;
  padding: 12px 0;
  font-size: 14px;
}
.scheme-import-dialog .el-dialog__footer {
  padding: 16px 40px 36px;
  border-top: 1px solid #ebeef0;
}
.scheme-import-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.scheme-import-footer .el-button {
  height: 32px;
  margin: 0;
  padding: 8px 16px;
  border-radius: 7px;
  font-size: 14px;
  font-weight: 400;
}
.scheme-import-footer .el-button--primary {
  --el-button-bg-color: #409eff;
  --el-button-border-color: #409eff;
  --el-button-hover-bg-color: #79bbff;
  --el-button-hover-border-color: #79bbff;
  --el-button-disabled-bg-color: #a0cfff;
  --el-button-disabled-border-color: #a0cfff;
  --el-button-disabled-text-color: #fff;
}
@media (max-width: 640px) {
  .scheme-import-overlay .scheme-import-dialog.el-dialog { width: calc(100% - 24px); max-height: calc(100dvh - 24px); }
  .scheme-import-dialog .el-dialog__header { padding: 24px 20px 16px; }
  .scheme-import-dialog .el-dialog__body { padding: 16px 20px; }
  .scheme-import-dialog .el-dialog__footer { padding: 16px 20px 24px; }
  .scheme-import-file-row { flex-wrap: wrap; gap: 8px 20px; }
}
</style>
