"""Verify result/run deletion using isolated browser history and mocked file operations."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--output-dir', type=Path, default=Path('.tmp/result-record-removal'))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
definition = {'nodes': [], 'links': [], 'version': 0.4}
workflow = {'id': 'workflow', 'name': '音频工作流', 'description': '', 'definition': definition,
            'format': 'graph', 'formatVersion': 1, 'createdAt': 1, 'updatedAt': 1}


def task(task_id, job_id, status, created, mode='workflow'):
    config = {'runMode': mode, 'outputFormat': 'wav', 'outputLayout': 'flat', 'inferenceParamsVersion': 1,
              'audioParams': {}, 'inferenceParams': {}, 'selectedStems': []}
    if mode == 'workflow':
        config.update(workflowId='workflow', workflowName=workflow['name'], workflowDefinition=definition,
                      workbench={'nativeGraph': True, 'sourceDefinition': definition, 'bindings': {
                          'inputs': {}, 'outputs': {}, 'primaryInput': '', 'outputLayout': 'flat'}})
    return {'id': task_id, 'jobId': job_id, 'input': 'D:/Music/' + task_id + '.wav', 'model': 'Audio',
            'output': 'D:/outputs', 'status': status, 'progress': 100, 'createdAt': created, 'updatedAt': created,
            'logs': ['[info] ' + task_id + ' log'], 'files': [],
            'outputs': [{'stem': 'Vocals', 'path': 'D:/outputs/' + task_id + '.wav'}] if status == 'done' else [],
            'runConfig': config}


records = [task('success', 'batch', 'done', 4), task('failed-sibling', 'batch', 'failed', 3),
           task('no-output', 'failed-run', 'failed', 1), task('single', 'single-run', 'done', 2, 'model'),
           {**task('legacy', 'legacy-run', 'done', 0), 'resultHidden': True}]
fixtures = {'app-settings': {'startupOnboardingSeen': True, 'locale': 'zh-CN', 'themeMode': 'light'},
            'task-history': {'tasks': records}, 'workflow-state': {'workflows': [workflow], 'selectedWorkflowId': 'workflow'}}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('for(const [name,value] of Object.entries(' + json.dumps(fixtures) + ''')) {
      const key='pymss-studio:'+name;
      if(!localStorage.getItem(key)) localStorage.setItem(key,JSON.stringify(value));
    }''')
    page.goto(args.url + '/#/results')
    page.wait_for_load_state('networkidle')
    expect(page.locator('.result-row')).to_have_count(2)

    def install_file_mocks():
        page.evaluate('''() => {
          window.__stores=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
          window.__removalCalls=[];
          window.__stores.get('editor').deleteProjectsForTasks=async ids=>{
            window.__removalCalls.push({type:'projects',ids});
            return {deletedProjectIds:ids.map(id=>'edit_'+id),missingProjectIds:[],blockedProjectIds:[],failedProjectIds:[]};
          };
          window.__stores.get('editor').listOpenProjectsForTasks=async()=>[];
          window.__stores.get('task').trashResultPaths=async paths=>{
            window.__removalCalls.push({type:'trash',paths});return {failed:[]};
          };
        }''')

    install_file_mocks()
    assert page.evaluate("window.__stores.get('task').tasks.some(task=>task.id==='legacy')") is False
    page.locator('#result-card-batch').get_by_role('button', name='删除', exact=True).click()
    dialog = page.locator('.n-dialog')
    expect(dialog).to_contain_text('任务日志和工作流运行记录')
    expect(dialog.get_by_role('checkbox')).not_to_be_checked()
    page.screenshot(path=str(args.output_dir / 'delete-result-confirmation.png'), animations='disabled')
    dialog.get_by_role('button', name='删除', exact=True).click()
    expect(page.locator('#result-card-batch')).to_have_count(0)
    assert page.evaluate("window.__stores.get('task').tasks.map(task=>task.id)") == ['no-output', 'single']
    assert page.evaluate('window.__removalCalls') == [{'type': 'projects', 'ids': ['success', 'failed-sibling']}]
    page.reload()
    page.wait_for_load_state('networkidle')
    expect(page.locator('.result-row')).to_have_count(1)
    install_file_mocks()
    assert page.evaluate("window.__stores.get('task').tasks.map(task=>task.id)") == ['no-output', 'single']

    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/workflow-node-editor?workflowId=workflow')")
    page.wait_for_url('**/workflow-node-editor?workflowId=workflow')
    page.add_style_tag(content='.title-bar{display:none!important}')
    page.get_by_role('button', name='日志与结果', exact=True).click()
    modal = page.locator('.workflow-run-details-modal')
    expect(modal.locator('.run-detail-selection label')).to_have_count(0)
    expect(modal.locator('.log-console')).to_contain_text('no-output log')
    expect(modal.get_by_role('button', name='删除运行记录', exact=True)).to_be_visible()
    page.screenshot(path=str(args.output_dir / 'failed-run-cleanup.png'), animations='disabled')
    modal.get_by_role('button', name='删除运行记录', exact=True).click()
    page.locator('.n-dialog').get_by_role('button', name='删除', exact=True).click()
    expect(modal).not_to_be_visible()
    expect(page.get_by_role('button', name='日志与结果', exact=True)).to_have_count(0)
    assert page.evaluate("window.__stores.get('task').tasks.map(task=>task.id)") == ['single']
    assert page.evaluate("window.__stores.get('workflow').workflows.length") == 1

    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/results')")
    page.wait_for_url('**/#/results')
    page.locator('#result-card-single-run').get_by_role('button', name='删除', exact=True).click()
    page.locator('.n-dialog .n-checkbox').click()
    page.locator('.n-dialog').get_by_role('button', name='删除', exact=True).click()
    expect(page.locator('.result-row')).to_have_count(0)
    assert page.evaluate('window.__removalCalls') == [
        {'type': 'trash', 'paths': ['D:/outputs/single.wav']}, {'type': 'projects', 'ids': ['single']}]
    assert page.evaluate("JSON.parse(localStorage.getItem('pymss-studio:task-history')).tasks") == []
    assert not errors, errors
    browser.close()
print('PASS: result deletion, failed siblings, optional recycling, persistence, legacy deletions and no-result run cleanup')
