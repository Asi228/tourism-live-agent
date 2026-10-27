<script setup>
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  ChatDotRound,
  CircleCheck,
  Collection,
  Connection,
  DataAnalysis,
  DocumentChecked,
  Lock,
  MagicStick,
  Promotion,
  ShoppingBag,
  Tickets,
} from '@element-plus/icons-vue'

import { getToken } from '../api/http'

const router = useRouter()

const features = [
  { icon: ShoppingBag, title: '旅游产品管理', text: '维护景区门票、酒店套餐、售卖规格、价格与可售库存。' },
  { icon: Promotion, title: '直播场次运营', text: '创建直播计划、配置本场商品，并形成开播准备清单。' },
  { icon: CircleCheck, title: '开播前检查', text: '自动发现库存不足、价格异常、活动失效和资料缺失问题。' },
  { icon: MagicStick, title: '智能运营建议', text: '结合场次数据和业务资料解释风险，给出处理优先级。' },
  { icon: ChatDotRound, title: '业务知识助手', text: '回答预约、入园、加价与退款问题，并展示引用来源。' },
  { icon: DocumentChecked, title: '风险操作审批', text: '改价、下架等操作先复核，再进入自动执行流程。' },
  { icon: Connection, title: '自动化处理', text: '执行采集、改价和下架任务，记录日志、截图与核验结果。' },
  { icon: DataAnalysis, title: '直播复盘', text: '汇总选品、检查结果、审批证据与自动化执行情况。' },
]

const flow = [
  ['01', '维护旅游产品', '录入景区门票、酒店套餐及售卖规则'],
  ['02', '创建直播场次', '选择本场商品，配置计划价格和库存'],
  ['03', '执行开播检查', '自动检查价格、库存、活动和资料完整性'],
  ['04', '处理风险事项', '智能解释原因，高风险操作进入人工审批'],
  ['05', '自动执行核验', '执行平台操作并保存结果与审计证据'],
]

const stack = [
  ['业务应用', 'Vue 3 · Element Plus · FastAPI'],
  ['数据服务', 'MySQL · Redis · Milvus'],
  ['智能能力', 'RAG 混合检索 · LangGraph · Function Calling'],
  ['自动化', 'Playwright · 任务队列 · 执行核验'],
  ['工程保障', 'JWT/RBAC · Alembic · SSE · Docker · 自动化测试'],
]

function enterSystem() {
  router.push(getToken() ? '/dashboard' : '/login')
}

function scrollToFlow() {
  document.querySelector('#workflow')?.scrollIntoView({ behavior: 'smooth' })
}
</script>

<template>
  <div class="intro-page">
    <header class="topbar">
      <a class="brand" href="#top" aria-label="返回顶部">
        <span class="brand-mark">旅</span>
        <span><strong>旅播智营</strong><small>旅游直播电商运营平台</small></span>
      </a>
      <nav>
        <a href="#value">项目背景</a>
        <a href="#workflow">业务流程</a>
        <a href="#features">核心功能</a>
        <a href="#technology">技术实现</a>
      </nav>
      <el-button type="primary" @click="enterSystem">
        {{ getToken() ? '返回工作台' : '进入演示系统' }}
      </el-button>
    </header>

    <main id="top">
      <section class="hero section-shell">
        <div class="hero-copy">
          <div class="eyebrow"><span /> 企业级场景实践项目</div>
          <h1>让旅游直播运营<br><em>从人工核对走向智能协同</em></h1>
          <p>
            面向景区门票、酒店套餐等旅游直播业务，统一管理产品、直播场次、业务知识、
            风险审批和自动化任务，减少规则分散、人工核对和操作留痕不足带来的运营风险。
          </p>
          <div class="hero-actions">
            <el-button type="primary" size="large" @click="enterSystem">
              进入演示系统 <el-icon><ArrowRight /></el-icon>
            </el-button>
            <el-button size="large" @click="scrollToFlow">查看业务流程</el-button>
            <span class="source-link source-private">企业项目演示 · 源码暂不公开</span>
          </div>
          <div class="hero-notes">
            <span><el-icon><CircleCheck /></el-icon> 完整业务闭环</span>
            <span><el-icon><CircleCheck /></el-icon> RAG + Agent</span>
            <span><el-icon><CircleCheck /></el-icon> 审批 + 自动化</span>
            <span><el-icon><Lock /></el-icon> 演示数据均为模拟数据</span>
          </div>
        </div>

        <div class="product-preview" aria-label="系统工作台示意图">
          <div class="preview-window">
            <div class="window-bar"><i /><i /><i /><span>旅播智营 · 运营工作台</span></div>
            <div class="preview-body">
              <aside>
                <b>旅</b>
                <span class="active" /><span /><span /><span /><span />
              </aside>
              <div class="preview-main">
                <div class="preview-head"><strong>今日运营总览</strong><i /></div>
                <div class="preview-hero">
                  <div><small>今日直播准备</small><strong>周末亲子门票专场</strong><span>距开播还有 2 小时</span></div>
                  <b>82<small>准备分</small></b>
                </div>
                <div class="preview-metrics">
                  <div><small>直播场次</small><strong>6</strong></div>
                  <div class="warn"><small>待处理风险</small><strong>3</strong></div>
                  <div><small>待审批</small><strong>2</strong></div>
                  <div><small>自动处理成功率</small><strong>96%</strong></div>
                </div>
                <div class="preview-grid">
                  <div class="preview-card">
                    <b>开播前检查</b>
                    <p><span class="danger-dot" /> 双人票直播价与计划不一致</p>
                    <p><span class="warning-dot" /> 周末库存余量低于预警线</p>
                    <p><span class="success-dot" /> 入园与退改资料完整</p>
                  </div>
                  <div class="preview-card assistant-card">
                    <b>智能运营建议</b>
                    <p>建议先确认直播价格，再补充周末库存。本次改价需要负责人审批。</p>
                    <span class="mini-button">生成审批草稿</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="float-card float-risk"><el-icon><CircleCheck /></el-icon><span><b>检查完成</b><small>发现 2 项待处理问题</small></span></div>
          <div class="float-card float-answer"><el-icon><ChatDotRound /></el-icon><span><b>答案可追溯</b><small>已引用 3 份业务资料</small></span></div>
        </div>
      </section>

      <section id="value" class="value-section section-shell">
        <div class="section-heading">
          <span>WHY</span>
          <h2>项目解决什么问题？</h2>
          <p>旅游产品规则复杂且变化频繁，直播团队需要把分散的信息和操作串成稳定流程。</p>
        </div>
        <div class="problem-grid">
          <article><strong>规则分散</strong><p>门票预约、酒店加价、节假日限制和退改政策散落在不同文档中。</p></article>
          <article><strong>人工核对</strong><p>开播前依赖运营逐项检查价格、库存和活动，容易遗漏关键风险。</p></article>
          <article><strong>口径不一致</strong><p>主播、运营和客服对同一产品的理解不同，影响讲解与售后处理。</p></article>
          <article><strong>操作难追溯</strong><p>改价和下架缺少审批、执行证据及结果核验，出现问题难以复盘。</p></article>
        </div>
      </section>

      <section id="workflow" class="workflow-section">
        <div class="section-shell">
          <div class="section-heading light">
            <span>WORKFLOW</span>
            <h2>从直播准备到执行复盘的完整闭环</h2>
            <p>系统围绕运营人员每天真实执行的工作组织功能，智能能力嵌入关键节点。</p>
          </div>
          <div class="flow-list">
            <article v-for="(item, index) in flow" :key="item[0]">
              <span class="flow-number">{{ item[0] }}</span>
              <div><strong>{{ item[1] }}</strong><p>{{ item[2] }}</p></div>
              <el-icon v-if="index < flow.length - 1"><ArrowRight /></el-icon>
            </article>
          </div>
        </div>
      </section>

      <section id="features" class="features-section section-shell">
        <div class="section-heading">
          <span>CAPABILITIES</span>
          <h2>每项功能都有明确的业务作用</h2>
          <p>传统运营管理、企业知识问答、审批控制和自动化执行共同组成完整系统。</p>
        </div>
        <div class="feature-grid">
          <article v-for="item in features" :key="item.title">
            <div class="feature-icon"><el-icon><component :is="item.icon" /></el-icon></div>
            <div><strong>{{ item.title }}</strong><p>{{ item.text }}</p></div>
          </article>
        </div>
      </section>

      <section id="technology" class="technology-section section-shell">
        <div class="tech-copy">
          <div class="section-heading align-left">
            <span>ENGINEERING</span>
            <h2>技术为业务流程服务</h2>
            <p>项目同时覆盖前后端开发、数据建模、智能检索、任务自动化、权限安全和工程测试。</p>
          </div>
          <div class="stack-list">
            <div v-for="item in stack" :key="item[0]"><strong>{{ item[0] }}</strong><span>{{ item[1] }}</span></div>
          </div>
        </div>
        <div class="architecture">
          <div class="architecture-title"><el-icon><Collection /></el-icon> 系统处理链路</div>
          <div class="arch-row"><span>Vue 运营端</span><span>FastAPI 服务</span><span>MySQL / Redis</span></div>
          <i />
          <div class="arch-row accent"><span>知识库检索</span><span>智能工作流</span><span>自动化执行器</span></div>
          <p>知识问答负责找准规则，智能工作流负责组织处理步骤，审批与自动化负责安全落地。</p>
        </div>
      </section>

      <section class="guide-section section-shell">
        <div>
          <span class="guide-label">5分钟体验路线</span>
          <h2>不用了解技术，也能看懂完整流程</h2>
          <p>登录后按照以下顺序操作，可以快速体验项目的主要能力。</p>
        </div>
        <ol>
          <li><b>1</b>查看旅游产品与直播场次</li>
          <li><b>2</b>运行一次开播前检查</li>
          <li><b>3</b>查看智能风险解释</li>
          <li><b>4</b>提交审批并查看自动化任务</li>
          <li><b>5</b>向知识助手询问退改规则</li>
        </ol>
        <el-button type="primary" size="large" @click="enterSystem">开始体验 <el-icon><ArrowRight /></el-icon></el-button>
      </section>
    </main>

    <footer>
      <div><strong>旅播智营</strong><span>旅游直播电商智能运营管理平台</span></div>
      <p>企业级场景实践项目 · 页面中的公司、商品及业务数据均为模拟数据</p>
    </footer>
  </div>
</template>

<style scoped>
.intro-page { min-height: 100%; overflow-x: hidden; color: #172033; background: #fff; }
.section-shell { width: min(1180px, calc(100% - 48px)); margin: 0 auto; }
.topbar {
  position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 34px;
  height: 72px; padding: 0 max(24px, calc((100% - 1180px) / 2));
  border-bottom: 1px solid rgb(226 232 240 / 80%); background: rgb(255 255 255 / 92%);
  backdrop-filter: blur(14px);
}
.brand { display: flex; align-items: center; gap: 10px; margin-right: auto; color: inherit; text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; color: #fff; background: linear-gradient(145deg, #2563eb, #0891b2); font-weight: 700; }
.brand strong, .brand small { display: block; }
.brand strong { font-size: 16px; }
.brand small { margin-top: 2px; color: #64748b; font-size: 10px; }
.topbar nav { display: flex; gap: 26px; }
.topbar nav a { color: #475569; font-size: 13px; text-decoration: none; }
.topbar nav a:hover { color: #2563eb; }
.hero { display: grid; grid-template-columns: .92fr 1.08fr; align-items: center; gap: 60px; min-height: 690px; padding-top: 60px; padding-bottom: 78px; }
.eyebrow { display: flex; align-items: center; gap: 8px; color: #2563eb; font-size: 12px; font-weight: 650; letter-spacing: .12em; }
.eyebrow span { width: 24px; height: 2px; background: #2563eb; }
.hero h1 { margin: 22px 0; color: #0f172a; font-size: clamp(38px, 4.2vw, 60px); line-height: 1.15; letter-spacing: -.045em; }
.hero h1 em { color: #2563eb; font-style: normal; }
.hero-copy > p { max-width: 590px; margin: 0; color: #64748b; font-size: 16px; line-height: 1.95; }
.hero-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 30px; }
.source-link { padding: 9px 10px; color: #475569; font-size: 13px; text-decoration: none; }
.source-link:hover { color: #2563eb; }
.source-private { cursor: default; }
.source-private:hover { color: #475569; }
.hero-notes { display: flex; flex-wrap: wrap; gap: 14px 20px; margin-top: 25px; color: #64748b; font-size: 11px; }
.hero-notes span { display: flex; align-items: center; gap: 5px; }
.hero-notes .el-icon { color: #0f9f6e; }
.product-preview { position: relative; }
.preview-window { overflow: hidden; border: 1px solid #dbe3ef; border-radius: 18px; background: #fff; box-shadow: 0 28px 70px rgb(15 23 42 / 16%); transform: perspective(1200px) rotateY(-2deg) rotateX(1deg); }
.window-bar { display: flex; align-items: center; gap: 6px; height: 38px; padding: 0 14px; border-bottom: 1px solid #e8edf4; background: #f8fafc; }
.window-bar i { width: 8px; height: 8px; border-radius: 50%; background: #cbd5e1; }
.window-bar i:first-child { background: #fb7185; }.window-bar i:nth-child(2) { background: #fbbf24; }.window-bar i:nth-child(3) { background: #34d399; }
.window-bar span { margin-left: 7px; color: #64748b; font-size: 9px; }
.preview-body { display: flex; height: 420px; }
.preview-body aside { display: flex; align-items: center; flex-direction: column; width: 54px; padding: 14px 9px; background: #0f172a; }
.preview-body aside b { display: grid; place-items: center; width: 28px; height: 28px; margin-bottom: 28px; border-radius: 8px; color: #fff; background: #2563eb; font-size: 12px; }
.preview-body aside span { width: 28px; height: 7px; margin-bottom: 19px; border-radius: 3px; background: #334155; }
.preview-body aside span.active { background: #38bdf8; }
.preview-main { flex: 1; min-width: 0; padding: 18px; background: #f3f6fb; }
.preview-head { display: flex; justify-content: space-between; margin-bottom: 14px; }
.preview-head strong { font-size: 12px; }.preview-head i { width: 56px; height: 10px; border-radius: 5px; background: #dbe3ef; }
.preview-hero { display: flex; align-items: center; justify-content: space-between; padding: 18px; border-radius: 10px; color: #fff; background: linear-gradient(120deg, #1e3a8a, #2563eb 60%, #0891b2); }
.preview-hero div small, .preview-hero div strong, .preview-hero div span { display: block; }
.preview-hero div small { opacity: .7; font-size: 8px; }.preview-hero div strong { margin: 5px 0; font-size: 14px; }.preview-hero div span { opacity: .82; font-size: 9px; }
.preview-hero > b { font-size: 28px; }.preview-hero > b small { display: block; font-size: 8px; font-weight: 400; opacity: .75; }
.preview-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }
.preview-metrics div { padding: 10px; border: 1px solid #e5eaf2; border-radius: 7px; background: #fff; }
.preview-metrics small, .preview-metrics strong { display: block; }.preview-metrics small { color: #94a3b8; font-size: 7px; }.preview-metrics strong { margin-top: 4px; font-size: 15px; }.preview-metrics .warn strong { color: #dc2626; }
.preview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.preview-card { min-height: 132px; padding: 13px; border: 1px solid #e5eaf2; border-radius: 8px; background: #fff; }
.preview-card > b { font-size: 10px; }.preview-card p { margin: 10px 0 0; color: #64748b; font-size: 8px; line-height: 1.5; }
.preview-card p span { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; }.danger-dot { background: #ef4444; }.warning-dot { background: #f59e0b; }.success-dot { background: #10b981; }
.assistant-card { background: #eff6ff; border-color: #bfdbfe; }.mini-button { display: inline-block; margin-top: 9px; padding: 5px 8px; border-radius: 4px; color: #fff; background: #2563eb; font-size: 7px; }
.float-card { position: absolute; display: flex; align-items: center; gap: 9px; padding: 11px 14px; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; box-shadow: 0 12px 30px rgb(15 23 42 / 14%); }
.float-card .el-icon { font-size: 20px; color: #10b981; }.float-card b, .float-card small { display: block; }.float-card b { font-size: 10px; }.float-card small { margin-top: 2px; color: #64748b; font-size: 8px; }
.float-risk { left: -30px; bottom: 52px; }.float-answer { right: -25px; top: 82px; }.float-answer .el-icon { color: #2563eb; }
.section-heading { max-width: 720px; margin: 0 auto 38px; text-align: center; }
.section-heading > span, .guide-label { color: #2563eb; font-size: 11px; font-weight: 700; letter-spacing: .16em; }
.section-heading h2 { margin: 12px 0; font-size: 31px; letter-spacing: -.025em; }.section-heading p { margin: 0; color: #64748b; line-height: 1.8; }
.value-section, .features-section, .technology-section { padding-top: 96px; padding-bottom: 96px; }
.problem-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.problem-grid article { padding: 24px; border: 1px solid #e5eaf2; border-radius: 12px; background: #f8fafc; }.problem-grid strong { font-size: 16px; }.problem-grid p { margin: 10px 0 0; color: #64748b; font-size: 13px; line-height: 1.8; }
.workflow-section { padding: 86px 0; color: #fff; background: #0f172a; }.section-heading.light h2 { color: #fff; }.section-heading.light p { color: #94a3b8; }.section-heading.light > span { color: #38bdf8; }
.flow-list { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }.flow-list article { position: relative; min-height: 180px; padding: 20px; border: 1px solid #334155; border-radius: 12px; background: #172033; }.flow-number { color: #38bdf8; font-size: 11px; font-weight: 700; }.flow-list strong { display: block; margin-top: 27px; font-size: 15px; }.flow-list p { margin: 9px 0 0; color: #94a3b8; font-size: 11px; line-height: 1.7; }.flow-list article > .el-icon { position: absolute; top: 50%; right: -20px; z-index: 2; color: #38bdf8; }
.feature-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }.feature-grid article { display: flex; gap: 14px; min-height: 142px; padding: 22px; border: 1px solid #e5eaf2; border-radius: 12px; transition: .2s ease; }.feature-grid article:hover { border-color: #93c5fd; box-shadow: 0 12px 28px rgb(37 99 235 / 8%); transform: translateY(-2px); }.feature-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: none; border-radius: 10px; color: #2563eb; background: #eff6ff; font-size: 19px; }.feature-grid strong { font-size: 14px; }.feature-grid p { margin: 8px 0 0; color: #64748b; font-size: 12px; line-height: 1.7; }
.technology-section { display: grid; grid-template-columns: 1fr 1fr; gap: 70px; align-items: center; }.align-left { margin: 0 0 25px; text-align: left; }.stack-list { display: flex; flex-direction: column; gap: 8px; }.stack-list div { display: grid; grid-template-columns: 90px 1fr; padding: 11px 13px; border-radius: 7px; background: #f8fafc; font-size: 12px; }.stack-list span { color: #64748b; }
.architecture { padding: 28px; border-radius: 16px; color: #fff; background: linear-gradient(145deg, #172554, #0f172a); box-shadow: 0 20px 50px rgb(15 23 42 / 15%); }.architecture-title { display: flex; align-items: center; gap: 8px; margin-bottom: 24px; font-weight: 650; }.arch-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.arch-row span { padding: 13px 8px; border: 1px solid #475569; border-radius: 7px; color: #cbd5e1; font-size: 10px; text-align: center; }.arch-row.accent span { border-color: #1d4ed8; color: #bfdbfe; background: rgb(37 99 235 / 14%); }.architecture > i { display: block; width: 1px; height: 22px; margin: 5px auto; background: #60a5fa; }.architecture p { margin: 22px 0 0; color: #94a3b8; font-size: 11px; line-height: 1.8; }
.guide-section { display: grid; grid-template-columns: 1fr 1.2fr auto; align-items: center; gap: 42px; margin-bottom: 90px; padding: 35px 40px; border: 1px solid #dbeafe; border-radius: 16px; background: #eff6ff; }.guide-section h2 { margin: 9px 0 6px; font-size: 23px; }.guide-section p { margin: 0; color: #64748b; font-size: 12px; }.guide-section ol { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin: 0; padding: 0; list-style: none; }.guide-section li { color: #475569; font-size: 11px; }.guide-section li b { display: inline-grid; place-items: center; width: 20px; height: 20px; margin-right: 6px; border-radius: 50%; color: #2563eb; background: #dbeafe; }
footer { display: flex; align-items: center; justify-content: space-between; padding: 30px max(24px, calc((100% - 1180px) / 2)); color: #94a3b8; background: #0f172a; }footer div { display: flex; flex-direction: column; gap: 3px; }footer strong { color: #fff; }footer span, footer p { margin: 0; font-size: 10px; }
@media (max-width: 980px) { .topbar nav { display: none; }.hero { grid-template-columns: 1fr; padding-top: 70px; }.product-preview { width: min(680px, 100%); margin: 0 auto; }.problem-grid, .feature-grid { grid-template-columns: repeat(2, 1fr); }.flow-list { grid-template-columns: 1fr 1fr; }.flow-list article > .el-icon { display: none; }.technology-section { grid-template-columns: 1fr; }.guide-section { grid-template-columns: 1fr; }.guide-section ol { grid-template-columns: 1fr 1fr; } }
@media (max-width: 620px) { .section-shell { width: min(100% - 28px, 1180px); }.topbar { height: 64px; padding: 0 14px; }.brand small { display: none; }.topbar > .el-button { padding: 8px 10px; }.hero { min-height: auto; gap: 42px; padding-top: 55px; }.hero h1 { font-size: 36px; }.hero-copy > p { font-size: 14px; }.float-card { display: none; }.preview-window { transform: none; }.preview-body { height: 350px; }.preview-metrics { grid-template-columns: 1fr 1fr; }.preview-grid { grid-template-columns: 1fr; }.preview-card:last-child { display: none; }.problem-grid, .feature-grid, .flow-list { grid-template-columns: 1fr; }.value-section, .features-section, .technology-section { padding-top: 70px; padding-bottom: 70px; }.section-heading h2 { font-size: 26px; }.guide-section { margin-bottom: 55px; padding: 25px; }.guide-section ol { grid-template-columns: 1fr; } footer { align-items: flex-start; flex-direction: column; gap: 12px; } }
</style>
