"""Chạy và chấm eval. expected chỉ cung cấp cho judge, không gửi cho agent."""
import argparse
import json
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from providers import Provider
from scenarios import ScenarioTools
from grading import grade_case, summarize, bonus_points
from bonus.tools import BonusTools


def run_case(case, args):
    scenario = case.get('scenario', 'normal')
    try:
        if getattr(args, 'bonus', False):
            tools = BonusTools('snapshot', scenario=scenario)
        else:
            tools = ScenarioTools(scenario, case.get('fail', False))
        result = run_agent(case['question'], Provider(args.provider, args.model),
                           tools, max_steps=args.max_steps)
    except Exception as exc:
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [],
                  'error': f'{type(exc).__name__}: {str(exc)[:200]}'}

    if not isinstance(result, dict) or not isinstance(result.get('trace', []), list):
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [],
                  'error': 'run_agent trả kết quả không hợp lệ.'}

    try:
        judge = Provider(args.judge_provider, args.judge_model)
        grade = grade_case(case, result, judge)
    except Exception as exc:
        grade = {'score': None, 'status': 'unscored', 'reason': str(exc), 'judge_usage': {}}

    return {'id': case['id'], 'question': case['question'], 'expected': case['expected'],
            'scenario': scenario, 'provider': args.provider, 'model': args.model,
            'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
            'result': result, 'grade': grade}


def main():
    parser = argparse.ArgumentParser(description='Chạy agent và chấm eval 0–100')
    choices = ['openai', 'gemini', 'anthropic', 'openrouter', 'opencode-go']
    parser.add_argument('--provider', choices=choices, default=os.getenv('PROVIDER', 'openai'))
    parser.add_argument('--model', default=os.getenv('MODEL'))
    parser.add_argument('--judge-provider', choices=choices, default=os.getenv('JUDGE_PROVIDER'))
    parser.add_argument('--judge-model', default=os.getenv('JUDGE_MODEL'))
    parser.add_argument('--bonus', action='store_true',
                        help='Chạy bộ bonus riêng; không ảnh hưởng điểm chính')
    parser.add_argument('--case', help='Ví dụ E2 hoặc H1; bỏ trống để chạy toàn bộ')
    parser.add_argument('--max-steps', type=int, default=None,
                        help='Giới hạn model calls mỗi case: mặc định 8, bonus 16')
    parser.add_argument('--workers', type=int, default=1,
                        help='Số case chạy đồng thời (mặc định 1)')
    parser.add_argument('--delay', type=float, default=0,
                        help='Thời gian nghỉ giữa các cases (giây)')
    parser.add_argument('--output', default='eval_results.json')
    parser.add_argument('--resume', action='store_true', help='Tiếp tục từ output đã lưu')
    parser.add_argument('--max-new', type=int, default=None, help='Tối đa case mới mỗi đợt')
    args = parser.parse_args()

    args.judge_provider = args.judge_provider or args.provider
    args.judge_model = args.judge_model or args.model
    if not args.model or not args.judge_model:
        parser.error('Thiếu --model hoặc MODEL')

    args.max_steps = args.max_steps if args.max_steps is not None else (16 if args.bonus else 8)
    if args.max_steps < 1 or not 1 <= args.workers <= 8:
        parser.error('--max-steps phải >= 1 và --workers phải từ 1 đến 8')
    if args.workers != 1:
        parser.error('Chế độ lưu từng case yêu cầu --workers 1')
    if args.max_new is None:
        args.max_new = 10**9 if args.bonus and not args.case else 3
    if args.max_new < 1:
        parser.error('--max-new phải >= 1')
    if args.delay < 0:
        parser.error('--delay phải >= 0')
    if args.delay > 0 and args.workers != 1:
        parser.error('--delay yêu cầu --workers 1')

    cases_path = (Path(__file__).resolve().parents[1] / 'bonus' / 'cases.json') if args.bonus \
        else (Path(__file__).parent / 'cases.json')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    selected = [c for c in cases if not args.case or c['id'] == args.case.upper()]
    if not selected:
        parser.error('Không tìm thấy case')

    def run_and_print(case):
        print(f"Đang chạy {case['id']}", flush=True)
        item = run_case(case, args)
        print(f"{case['id']}: {item['grade'].get('score')} ({item['grade']['status']})", flush=True)
        return item

    # Chỉ tái sử dụng case đã chấm điểm; case lỗi sẽ được chạy lại ở đợt sau.
    saved = {}
    output_path = Path(args.output)
    if args.resume and output_path.exists():
        previous = json.loads(output_path.read_text(encoding='utf-8'))
        old_config = previous.get('config', {})
        expected_config = {'provider': args.provider, 'model': args.model,
                           'judge_provider': args.judge_provider,
                           'judge_model': args.judge_model,
                           'max_steps': args.max_steps,
                           'dataset': 'bonus' if args.bonus else 'core'}
        if old_config != expected_config:
            parser.error('Cấu hình file cũ khác cấu hình hiện tại; chọn --output mới')
        saved = {item['id']: item for item in previous.get('cases', [])
                 if item.get('grade', {}).get('status') == 'scored'}

    config = {'provider': args.provider, 'model': args.model,
              'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
              'max_steps': args.max_steps,
              'dataset': 'bonus' if args.bonus else 'core'}

    def save_progress():
        results = [saved[c['id']] for c in selected if c['id'] in saved]
        report = {'summary': summarize(results), 'config': config, 'cases': results}
        if args.bonus:
            full_run = (not args.case and len(results) == len(cases))
            report['summary']['bonus_points_0_15'] = bonus_points(
                report['summary']['score_0_100'], full_run)
        temp_path = output_path.with_name(output_path.name + '.tmp')
        temp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
        temp_path.replace(output_path)
        return report

    count = 0
    for case in selected:
        if case['id'] in saved:
            print(f"Bỏ qua {case['id']} (đã scored)", flush=True)
            continue
        if count >= args.max_new:
            break
        if count > 0 and args.delay > 0:
            time.sleep(args.delay)
        item = run_and_print(case)
        saved[case['id']] = item
        save_progress()  # lưu ngay sau từng case, kể cả khi case lỗi
        count += 1
        if item['grade'].get('status') != 'scored':
            print('Case chưa scored; dừng đợt để tránh tiêu hao quota.', flush=True)
            break

    report = save_progress()
    summary = report['summary']
    print(f"Đã lưu {len(report['cases'])}/{len(selected)} cases; "
          f"scored {summary['scored_cases']}; file: {args.output}")
    return 0 if len(report['cases']) == len(selected) and summary['unscored_cases'] == 0 else 1



if __name__ == '__main__':
    sys.exit(main())
