"""Check cleanup of persisted model and Ensemble runs without result records."""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--output-dir', type=Path, default=Path('.tmp/separation-record-cleanup'))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)


def task(index):
    return {'id': f'task-{index}', 'jobId': f'job-{index}', 'model': 'Ensemble' if index % 2 else 'Model',
            'input': f'D:/Music/{index}.wav', 'output': 'D:/outputs', 'status': 'cancelled' if index % 2 else 'failed',
            'progress': 100, 'createdAt': 1000 + index, 'updatedAt': 1000 + index,
            'logs': [f'[error] Run {index} failed'], 'outputs': [], 'files': [],
            'runConfig': {'runMode': 'workflow' if index % 2 else 'model', 'outputFormat': 'wav',
                          'audioParams': {}, 'inferenceParams': {}, 'inferenceParamsVersion': 1, 'selectedStems': []}}


history = [task(index) for index in range(100)]
result = {**task(100), 'status': 'done', 'outputs': [{'stem': 'Vocals', 'path': 'D:/outputs/result.wav'}]}
workflow = task(101)
workflow['runConfig']['workbench'] = {'sourceDefinition': {'version': 1, 'steps': []}, 'bindings': {
    'inputs': {}, 'outputs': {}, 'primaryInput': '', 'outputLayout': 'flat'}}
workflow['runConfig']['workflowId'] = 'editor-workflow'
fixtures = {'app-settings': {'startupOnboardingSeen': True, 'locale': 'zh-CN', 'themeMode': 'light'},
            'task-history': {'tasks': history + [result, workflow]}}

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
    page.goto(args.url + '/#/separate')
    page.wait_for_load_state('networkidle')

    def open_history():
        page.get_by_role('button', name=re.compile(r'^任务队列')).first.click()
        modal = page.locator('.batch-queue-modal')
        modal.locator('.n-radio-button').filter(has_text='无结果记录').click()
        return modal

    page.locator('.stage-head__queue').click()
    queue = page.locator('.batch-queue-modal')
    expect(queue.locator('.batch-queue-row')).to_have_count(0)
    queue.locator('.n-radio-button').filter(has_text='无结果记录').click()
    expect(queue.locator('.batch-queue-row')).to_have_count(100)
    page.screenshot(path=str(args.output_dir / 'history.png'), animations='disabled')
    queue.locator('.batch-queue-row--failed').first.get_by_role('button', name='日志', exact=True).click()
    expect(page.locator('.log-console')).to_contain_text('Run 98 failed')
    page.keyboard.press('Escape')
    expect(page.locator('.log-console')).to_have_count(0)

    queue = open_history()
    queue.locator('.batch-queue-row').first.get_by_role('button', name='删除运行记录', exact=True).click()
    expect(page.locator('.n-dialog')).to_contain_text('输出文件不受影响')
    page.locator('.n-dialog').get_by_role('button', name='删除', exact=True).click()
    expect(queue.locator('.batch-queue-row')).to_have_count(99)
    queue.get_by_role('button', name='清空记录', exact=True).click()
    expect(page.locator('.n-dialog')).to_contain_text('99 次运行记录')
    page.locator('.n-dialog').get_by_role('button', name='删除', exact=True).click()
    expect(queue.locator('.batch-queue-row')).to_have_count(0)
    assert page.evaluate("JSON.parse(localStorage.getItem('pymss-studio:task-history')).tasks.map(task=>task.id)") == ['task-100', 'task-101']
    page.reload()
    page.wait_for_load_state('networkidle')
    queue = open_history()
    expect(queue.locator('.batch-queue-row')).to_have_count(0)
    expect(queue.get_by_text('暂无无结果运行记录', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    page.evaluate("document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/results')")
    page.wait_for_url(re.compile(r'/#/results$'))
    expect(page.locator('.result-row')).to_have_count(1)
    assert not errors, errors
    browser.close()
print('PASS: historical model/Ensemble logs, single and batch cleanup, restart persistence and result/workflow isolation')
