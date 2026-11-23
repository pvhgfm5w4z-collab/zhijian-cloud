import os
import tempfile
import unittest
from unittest.mock import patch

import agent_core as AG
import demo_app as app


class BaselineTest(unittest.TestCase):
    def test_registered_v3_uses_field_v2_for_comparison_and_ledger(self):
        with tempfile.TemporaryDirectory() as root, patch.object(AG, 'PROJECTS_DIR', root):
            AG.create_project('test', '施工阶段', '固定单价合同')
            for n in range(1, 4):
                path = os.path.join(root, f'input{n}.dxf')
                with open(path, 'w') as stream:
                    stream.write(str(n))
                AG.register_version('test', 'A', path, '')
            self.assertTrue(AG.set_version_status('test', 'A', '', 'v2', '已批准'))
            self.assertTrue(AG.set_version_status('test', 'A', '', 'v2', '现场使用'))
            change = dict(idx=1, kind='ADDED', kind_cn='新增', type='HATCH', layer='0', new='', bbox=[0, 0, 1000, 2000])

            def compare(jid, old, new):
                self.assertEqual(old, AG.get_version_file('test', 'A', 'v2'))
                self.assertEqual(new, AG.get_version_file('test', 'A', 'v3'))
                job = dict(counts={}, changes=[change], dir=root)
                app.JOBS[jid] = job
                self.addCleanup(app.JOBS.pop, jid, None)
                return job

            with patch.object(app, '_run_compare', side_effect=compare), \
                 patch.object(AG, 'generate_report', return_value={}) as report, \
                 patch.object(AG, 'archive_job', return_value={}) as archive, \
                 patch.object(AG, 'load_llm_config', return_value=None):
                result = app._run_agent_workflow(dict(project='test', drawing_no='A', target_version='v3'))
            self.assertEqual(result['run_ctx']['ver_old'], 'v2')
            self.assertEqual(result['run_ctx']['ver_new'], 'v3')
            self.assertEqual(result['ledger']['items'][0]['area_delta_m2'], 2)
            self.assertEqual(result['ledger']['summaries'][0]['items'][0]['area_delta_m2'], 2)
            self.assertEqual(report.call_args.args[3:5], ('v2', 'v3'))
            self.assertEqual(archive.call_args.args[4:6], ('v2', 'v3'))
            source = AG.get_ledger('test')['items'][0]['source']
            self.assertEqual((source['from_version'], source['to_version']), ('v2', 'v3'))
            stale = app._run_agent_workflow(dict(project='test', drawing_no='A', target_version='v3', baseline_version='v1'))
            self.assertEqual(stale.status_code, 400)
            invalid = app._run_agent_workflow(dict(project='test', drawing_no='A', target_version='v1'))
            self.assertEqual(invalid.status_code, 400)


class LedgerThicknessTest(unittest.TestCase):
    def test_cleanup_removes_generated_files_and_keeps_versions_and_shared_jobs(self):
        with tempfile.TemporaryDirectory() as root, patch.object(AG, 'PROJECTS_DIR', os.path.join(root, 'projects')), patch.object(app, 'JOBS_DIR', os.path.join(root, 'jobs')):
            os.makedirs(AG.PROJECTS_DIR)
            os.makedirs(app.JOBS_DIR)
            AG.create_project('test', '施工阶段', '固定单价合同')
            directory = AG._proj_dir('test')
            job = 'cleanup-test'
            arc = os.path.join(directory, 'archive', job)
            os.makedirs(arc)
            job_dir = os.path.join(app.JOBS_DIR, job)
            os.makedirs(job_dir)
            report = os.path.join(directory, 'reports', f'diff_report_{job}.md')
            drawing = os.path.join(directory, 'drawings', 'v1.dxf')
            for path in (report, drawing, os.path.join(arc, 'visa.docx'), os.path.join(job_dir, 'visa.docx')):
                with open(path, 'w') as stream:
                    stream.write('test')
            AG._save_json(os.path.join(directory, 'archive', 'index.json'), {'archives': [dict(job_id=job, runs=[dict(job_id=job), dict(job_id='shared')])]})
            app._delete_visa_files('test', job, [dict(source=dict(job_id='shared'))])
            self.assertTrue(os.path.isdir(arc))
            self.assertTrue(os.path.isfile(drawing))
            app._delete_visa_files('test', job, [])
            self.assertFalse(os.path.exists(arc))
            self.assertFalse(os.path.exists(job_dir))
            self.assertFalse(os.path.exists(report))
            self.assertTrue(os.path.isfile(drawing))
            self.assertEqual(AG.list_archive('test')['archives'], [])

    def test_delete_updates_totals_and_does_not_reappear_on_rerun(self):
        with tempfile.TemporaryDirectory() as root, patch.object(AG, 'PROJECTS_DIR', root):
            AG.create_project('test', '施工阶段', '固定单价合同')
            changes = [dict(idx=n, kind='ADDED', kind_cn='新增', type='HATCH', layer='wall',
                            new=f'area {n}', bbox=[0, 0, 1000, 2000]) for n in range(2)]
            estimates = [dict(idx=n, delta=1, unit='m³', price=100, amount=100) for n in range(2)]
            def update():
                return AG.update_ledger('test', 'delete-job', 'A', 'v2', 'v3', changes, estimates, AG.get_project('test'))
            update()
            AG.update_ledger('test', 'other-job', 'B', 'v1', 'v2', changes[:1], estimates[:1], AG.get_project('test'))
            summaries = AG.get_ledger('test')['summaries']
            visa_id = summaries[0]['id']
            other_ids = [i['id'] for i in summaries[1]['items']]
            self.assertEqual(app.api_ledger_delete('test', visa_id), {'ok': True})
            ledger = AG.get_ledger('test')
            self.assertEqual(ledger['stats']['amount_sum'], 100)
            self.assertEqual([i['id'] for i in ledger['items']], other_ids)
            self.assertEqual(update()['added'], 0)
            self.assertEqual(app.api_ledger_delete('test', visa_id).status_code, 404)
            app.api_ledger_delete('test', summaries[1]['id'])
            self.assertEqual(AG.get_ledger('test')['summaries'], [])

    def test_line_requires_confirmed_contract_instead_of_default_price(self):
        change = dict(idx=0, kind='ADDED', kind_cn='新增', type='LINE', layer='wall',
                      new='线条 长度=200.0', old='')
        source = dict(drawing_no='A', sample_key='')
        unmatched = app._contract_estimate(change, source, [])
        self.assertEqual(unmatched['amount'], 0)
        self.assertEqual(unmatched['price'], '-')
        confirmed = dict(source=source, layer='wall', type='LINE',
                         estimate=dict(boq_code='wall', boq_name='砌块墙', unit='m³', price=430))
        matched = app._contract_estimate(change, source, [confirmed])
        self.assertEqual(matched['price'], 430)
        self.assertEqual(matched['unit'], 'm³')
        self.assertEqual(matched['amount'], 0)
        self.assertTrue(matched['needs_review'])

    def test_volume_price_persists_and_rejects_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as root, patch.object(AG, 'PROJECTS_DIR', root):
            AG.create_project('test', '施工阶段', '固定单价合同')
            change = dict(idx=0, kind='DELETED', kind_cn='删除', type='HATCH', layer='wall',
                          old='', new='', bbox=[0, 0, 2000, 3000])
            AG.update_ledger('test', 'test-thickness', 'A', 'v2', 'v3', [change],
                             [app.estimate_change(change, boq_item=dict(code='001', name='砌块墙', unit='m³', unit_price=430))], AG.get_project('test'))
            item = app.api_ledger('test')['items'][0]
            payload = dict(item_id=item['id'], thickness_m=0.2)
            for field, value in [('thickness_m', 0), ('thickness_m', -1),
                                 ('thickness_m', 'nan')]:
                self.assertEqual(app.api_ledger_price('test', {**payload, field: value}).status_code, 400)
            result = app.api_ledger_price('test', payload)
            self.assertEqual(result['estimate']['delta'], '-1.200000')
            self.assertEqual(result['estimate']['amount'], -516)
            self.assertEqual(app.api_ledger('test')['summaries'][0]['amount'], -516)
            saved = app.api_ledger('test')['items'][0]
            self.assertEqual(saved['estimate']['thickness_m'], 0.2)
            # A thickness must never turn an area-priced item into a volume-priced item.
            area_price = app.estimate_change(change, boq_item=dict(code='a', name='面层', unit='m²', unit_price=85), thickness_m=0.2)
            self.assertEqual(area_price['amount'], -510)
            self.assertTrue(AG.set_ledger_status('test', item['id'], '已确认'))
            self.assertEqual(app.api_ledger_price('test', payload).status_code, 400)

    def test_inherit_confirmed_mapping_only_in_same_drawing_chain(self):
        source = dict(drawing_no='A', sample_key='', job_id='new')
        item = dict(source=source, type='HATCH', layer='CONC', estimate={})
        confirmed = dict(source={**source, 'job_id': 'old'}, type='HATCH', layer='CONC',
                         estimate=dict(boq_code='001', boq_name='C30混凝土', unit='m³', price=520))
        self.assertEqual(app._ledger_boq(item, [confirmed])['unit_price'], 520)
        self.assertIsNone(app._ledger_boq(item, [{**confirmed, 'source': dict(drawing_no='B')}]))
        conflict = {**confirmed, 'estimate': {**confirmed['estimate'], 'price': 600}}
        self.assertIsNone(app._ledger_boq(item, [confirmed, conflict]))

    def test_select_existing_project_boq_and_save_thickness(self):
        with tempfile.TemporaryDirectory() as root, patch.object(AG, 'PROJECTS_DIR', root):
            AG.create_project('test', '施工阶段', '固定单价合同')
            change = dict(idx=0, kind='ADDED', kind_cn='新增', type='HATCH', layer='unmapped',
                          old='', new='', bbox=[0, 0, 1000, 2000])
            AG.update_ledger('test', 'selection-job', 'A', 'v2', 'v3', [change],
                             [app.estimate_change(change)], AG.get_project('test'))
            with patch.dict(app.JOBS, {'selection-job': dict(boq_items=[
                dict(code='C30', name='C30混凝土', unit='m³', unit_price=520),
                dict(code='area', name='面层', unit='m²', unit_price=85)])}):
                ledger = app.api_ledger('test')
                self.assertEqual(len(ledger['boq_options']), 1)
                item = ledger['items'][0]
                self.assertIsNone(item['pricing_boq'])
                payload = dict(item_id=item['id'], thickness_m=0.2, boq_choice='invalid')
                self.assertEqual(app.api_ledger_price('test', payload).status_code, 400)
                payload['boq_choice'] = ledger['boq_options'][0]['key']
                payload['unit_price'] = 1  # Client prices cannot override the stored contract.
                result = app.api_ledger_price('test', payload)
                self.assertEqual(result['estimate']['amount'], 208)
                saved = app.api_ledger('test')['items'][0]
                self.assertEqual(saved['pricing_boq']['unit_price'], 520)
                self.assertEqual(saved['estimate']['thickness_m'], 0.2)

    def test_legacy_rows_restore_geometry_and_confirmed_boq(self):
        change = dict(idx=0, kind='ADDED', kind_cn='新增', type='HATCH', layer='wall', bbox=[0, 0, 1000, 2000])
        item = dict(source=dict(job_id='legacy-thickness'), kind='ADDED', type='HATCH', layer='wall',
                    estimate=dict(delta='+0.4', unit='m³', price=430, amount=172))
        with patch.dict(app.JOBS, {'legacy-thickness': dict(changes=[change], visa_items=[dict(idx=0, boq_code='001', boq_name='墙', thickness_m=0.2)])}):
            app._restore_ledger_geometry([item])
        self.assertEqual(item['area_delta_m2'], 2)
        self.assertEqual(item['estimate']['boq_code'], '001')
        self.assertEqual(item['estimate']['thickness_m'], 0.2)


if __name__ == '__main__':
    unittest.main()
