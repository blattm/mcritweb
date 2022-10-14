import os
import re
import datetime
import hashlib
from datetime import datetime
from mcrit.client.McritClient import McritClient
from mcrit.storage.MatchingResult import MatchingResult
from flask import current_app, Blueprint, render_template, request, redirect, url_for, Response, flash, session, send_from_directory, json

from mcritweb.views.authentication import visitor_required, contributor_required
from mcritweb.views.cross_compare import get_sample_to_job_id, score_to_color
from mcritweb.views.utility import get_server_url, mcrit_server_required, parseBitnessFromFilename, parseBaseAddrFromFilename, get_matches_node_colors
from mcritweb.views.pagination import Pagination
from mcritweb.views.MatchReportRenderer import MatchReportRenderer
from mcritweb.views.ScoreColorProvider import ScoreColorProvider
from mcritweb.views.analyze import query as analyze_query

bp = Blueprint('data', __name__, url_prefix='/data')

################################################################
# Helper functions
################################################################

def load_cached_result(app, job_id):
    matching_result = {}
    cache_path = os.sep.join([app.instance_path, "cache", "results"])
    for filename in os.listdir(cache_path):
        if job_id in filename and filename.endswith("json"):
            with open(cache_path + os.sep + filename, "r") as fin:
                matching_result = json.load(fin)
    return matching_result


def cache_result(app, job_info, matching_result):
    # TODO potentially implement a cache control that manages maximum allowed cache size?
    if job_info.result is not None:
        cache_path = os.sep.join([app.instance_path, "cache", "results"])
        timestamped_filename = datetime.utcnow().strftime(f"%Y%m%d-%H%M%S-{job_info.job_id}.json")
        with open(cache_path + os.sep + timestamped_filename, "w") as fout:
            json.dump(matching_result, fout, indent=1)


def create_match_diagram(app, job_id, matching_result, filtered_family_id=None, filtered_sample_id=None):
    cache_path = os.sep.join([app.instance_path, "cache", "diagrams"])
    family_sample_suffix = ""
    if filtered_family_id is not None:
        family_sample_suffix = f"-famid_{filtered_family_id}"
    elif filtered_sample_id is not None:
        family_sample_suffix = f"-samid_{filtered_sample_id}"
    output_path = cache_path + os.sep + job_id + family_sample_suffix + ".png"
    if not os.path.isfile(output_path):
        renderer = MatchReportRenderer()
        renderer.processReport(matching_result)
        image = renderer.renderStackedDiagram(filtered_family_id=filtered_family_id, filtered_sample_id=filtered_sample_id)
        image.save(output_path)

# https://stackoverflow.com/a/39842765
# https://stackoverflow.com/a/26972238
# https://flask.palletsprojects.com/en/1.0.x/api/#flask.send_from_directory
@bp.route('/diagrams/<path:filename>')
def diagram_file(filename):
    cache_path = os.sep.join([current_app.instance_path, "cache", "diagrams"])
    return send_from_directory(cache_path, filename)

def _parse_integer_query_param(request, query_param:str):
    """ Try to find query_param in the request and parse it as int """
    param = None
    try:
        param = int(request.args.get(query_param))
    except Exception:
        pass
    return param

################################################################
# Import + Export
################################################################

@bp.route('/import',methods=('GET', 'POST'))
@mcrit_server_required
@contributor_required
def import_view():
    if request.method == 'POST':
        f = request.files.get('file', '')
        client = McritClient(mcrit_server=get_server_url())
        session["last_import"] = client.addImportData(json.load(f))
    return render_template("import.html")

@bp.route('/import_complete')
@contributor_required
def import_complete():
    import_results = session.pop('last_import',{})
    if import_results:
        return render_template("import_complete.html", results=import_results)
    else:
        flash("This doesn't seem to be valid MCRIT data in JSON format", category='error')
        return render_template("import.html")


@bp.route('/export',methods=('GET', 'POST'))
@mcrit_server_required
@contributor_required
def export_view():
    if request.method == 'POST':
        requested_samples = request.form['samples']
        client = McritClient(mcrit_server=get_server_url())
        if requested_samples == "":
            export_file = json.dumps(client.getExportData())
            return Response(
                export_file,
                mimetype='application/json',
                headers={"Content-disposition":
                        "attachment; filename=export_all_samples.json"})
        # NOTE it might be nice to allow [<number>, <number>-<number>, ...] to enable 
        # spans of consecutive sample_ids
        elif re.match("^\d+(?:[\s]*,[\s]*\d+)*$", requested_samples):
            sample_ids = [int(sample_id.strip()) for sample_id in requested_samples.split(',')]
            export_file = json.dumps(client.getExportData(sample_ids))
            return Response(
                export_file,
                mimetype='application/json',
                headers={"Content-disposition":
                        "attachment; filename=export_samples.json"})
        else:
            flash('Please use a comma-separated list of sample_ids in your export request.', category='error')
            return render_template("export.html")
    return render_template("export.html")

@bp.route('/specific_export/<type>/<item_id>')
@mcrit_server_required
@contributor_required
def specific_export(type, item_id):
    client = McritClient(mcrit_server= get_server_url())
    if type == 'family':
        samples = client.getSamplesByFamilyId(item_id)
        sample_ids = [x.sample_id for x in samples]
        export_file = json.dumps(client.getExportData(sample_ids))
        return Response(
            export_file,
            mimetype='application/json',
            headers={"Content-disposition":
                    "attachment; filename=export_family_"+str(item_id)+".json"})
    if type == 'samples':
        sample_ids = []
        sample_entry = client.getSampleById(item_id)
        if sample_entry:
            sample_ids.append(sample_entry.sample_id)
        export_file = json.dumps(client.getExportData(sample_ids))
        return Response(
            export_file,
            mimetype='application/json',
            headers={"Content-disposition":
                    "attachment; filename=export_samples.json"})

################################################################
# Result presentation
################################################################



@bp.route('/result/<job_id>')
@mcrit_server_required
@visitor_required
# TODO:  refactor, simplify
def result(job_id):
    client = McritClient(mcrit_server=get_server_url())
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = client.getJobData(job_id)
    if not result_json:
        # otherwise obtain result report from remote
            result_json = client.getResultForJob(job_id)
            if result_json:
                cache_result(current_app, job_info, result_json)
    if result_json:
        score_color_provider = ScoreColorProvider()
        # TODO validation - only parse to matching_result if this data type is appropriate 
        # re-format result report for visualization and choose respective template
        if job_info is None:
            return render_template("result_invalid.html", job_id=job_id)
        if job_info.parameters.startswith("getMatchesForSampleVs"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForSample"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForSmdaReport"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForMappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("getMatchesForUnmappedBinary"):
            matching_result = MatchingResult.fromDict(result_json)
            return result_matches_for_sample_or_query(job_info, matching_result)
        elif job_info.parameters.startswith("combineMatchesToCross"):
            return result_matches_for_cross(job_info, result_json)
        # NOTE: 'updateMinHashes' is the start of 'updateMinHashesForSample'.
        # For this reason, these two elif clauses may not be reordered
        elif job_info.parameters.startswith("updateMinHashesForSample"):
            raise NotImplementedError("Implement me")
        elif job_info.parameters.startswith("updateMinHashes"):
            raise NotImplementedError("Implement me")
        elif job_info.parameters.startswith("getUniqueBlocks"):
            return result_unique_blocks(job_info, result_json)
        elif job_info.parameters.startswith("addBinarySample"):
            return redirect(url_for('explore.sample_by_id', sample_id=result_json['sample_info']['sample_id']))
    elif job_info:
        # if we are not done processing, list job data
        return render_template("job_in_progress.html", job_info=job_info)
    else:
        # if we can't find job or result, we have to assume the job_id was invalid
        return render_template("result_invalid.html", job_id=job_id)

@bp.route('/yarafication/<job_id>', methods=['POST'])
@mcrit_server_required
@visitor_required
def yarafication(job_id):
    client = McritClient(mcrit_server=get_server_url())
    # check if we have the respective report already locally cached
    result_json = load_cached_result(current_app, job_id)
    job_info = client.getJobData(job_id)
    if not result_json:
        # otherwise obtain result report from remote
            result_json = client.getResultForJob(job_id)
            if result_json:
                cache_result(current_app, job_info, result_json)
    if result_json:
        # TODO validation - only parse to matching_result if this data type is appropriate 
        # re-format result report for visualization and choose respective template
        if job_info is None:
            return render_template("result_invalid.html", job_id=job_id)
    query = None
    if request.method == 'POST' and "block_count" in request.form:
        query = request.form['block_count']
    selected_picblocks = []
    if query:
        # TODO build rule with greedy algorithm
        pass
    yara_rule = query
    return render_template("result_blocks_yara.html", job_info=job_info, yara_rule=yara_rule)

def result_unique_blocks(job_info, blocks_result: dict):
    payload_params = json.loads(job_info.payload["params"])
    sample_ids = payload_params["0"]
    sample_id = sample_ids[0]
    family_id = None
    if "family_id" in payload_params:
        family_id = payload_params["family_id"]
    if blocks_result is None:
        if family_id is not None:
            flash(f"Ups, no results for unique blocks in family with id {family_id}", category="error")
        else:
            flash(f"Ups, no results for unique blocks in family with id {sample_id}", category="error")
    number_of_unique_blocks = 0
    paginated_blocks = []
    if blocks_result is not None:
        min_score = _parse_integer_query_param(request, "min_score")
        min_block_length = _parse_integer_query_param(request, "min_block_length")
        max_block_length = _parse_integer_query_param(request, "max_block_length")
        filtered_blocks = blocks_result
        if min_score:
            filtered_blocks = {pichash: block for pichash, block in blocks_result.items() if block["score"] >= min_score}
        if min_block_length or max_block_length:
            min_block_length = 0 if min_block_length is None else min_block_length
            max_block_length = 0xFFFFFFFF if max_block_length is None else max_block_length
            filtered_blocks = {pichash: block for pichash, block in filtered_blocks.items() if max_block_length >= block["length"] >= min_block_length}
        blocks_result = filtered_blocks
        number_of_unique_blocks = len(blocks_result)
        block_pagination = Pagination(request, number_of_unique_blocks, limit=100, query_param="blkp")
        index = 0
        for pichash, result in sorted(blocks_result.items(), key=lambda x: x[1]["score"], reverse=True):
            if index >= block_pagination.end_index:
                break
            if index >= block_pagination.start_index:
                yarafied = f"/* picblockhash: {pichash} \n"
                maxlen_ins = max([len(ins[1]) for ins in result["instructions"]])
                for ins in result["instructions"]:
                    yarafied += f" * {ins[1]:{maxlen_ins}} | {ins[2]} {ins[3]}\n"
                yarafied += " */\n"
                yarafied += "{ " + re.sub("(.{80})", "\\1\n", result["escaped_sequence"], 0, re.DOTALL) + " }"
                blocks_result[pichash]["yarafied"] = yarafied
                paginated_block = result
                paginated_block["key"] = pichash
                paginated_block["yarafied"] = yarafied
                paginated_blocks.append(paginated_block)
            index += 1
    if family_id is not None:
        return render_template("result_blocks_family.html", job_info=job_info, family_id=family_id, number_of_unique_blocks=number_of_unique_blocks, results=paginated_blocks, blkp=block_pagination)
    else:
        return render_template("result_blocks_sample.html", job_info=job_info, sample_id=sample_id, number_of_unique_blocks=number_of_unique_blocks, results=paginated_blocks, blkp=block_pagination)

def result_matches_for_sample_or_query(job_info, matching_result: MatchingResult):
    score_color_provider = ScoreColorProvider()
    # TODO think of additional useful query parameters that can be used to filter/shape the result output
    filtered_sample_id = _parse_integer_query_param(request, "samid")
    filtered_family_id = _parse_integer_query_param(request, "famid")
    filtered_function_id = _parse_integer_query_param(request, "funid")
    other_function_id = _parse_integer_query_param(request, "ofunid")
    client = McritClient(mcrit_server=get_server_url())

    if filtered_family_id is not None and client.isFamilyId(filtered_family_id):
        matching_result.filterToFamilyId(filtered_family_id)
        create_match_diagram(current_app, job_info.job_id, matching_result, filtered_family_id=filtered_family_id)
        num_samples_matched = len(matching_result.sample_matches)
        sample_pagination = Pagination(request, num_samples_matched, limit=10, query_param="samp")
        function_pagination = Pagination(request, len(matching_result.getAggregatedFunctionMatches()), query_param="funp")
        return render_template("result_compare_family.html", famid=filtered_family_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider) 
    elif filtered_sample_id is not None and client.isSampleId(filtered_sample_id):
        matching_result.filterToSampleId(filtered_sample_id)
        create_match_diagram(current_app, job_info.job_id, matching_result, filtered_sample_id=filtered_sample_id)
        filtered_sample_entry = client.getSampleById(filtered_sample_id)
        matching_result.other_sample_entry = filtered_sample_entry
        num_functions_matched = len(matching_result.function_matches)
        sample_pagination = Pagination(request, 1, limit=10, query_param="samp")
        function_pagination = Pagination(request, len(matching_result.getAggregatedFunctionMatches()), query_param="funp")
        return render_template("result_compare_sample.html", samid=filtered_sample_id, job_info=job_info, samp=sample_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider) 
    # treat family/sample part as if there was no filter
    elif filtered_function_id is not None and other_function_id is not None and client.isFunctionId(filtered_function_id) and client.isFunctionId(other_function_id):
        # TODO: get minhash matching score and also show in resulting HTML
        function_entry = client.getFunctionById(filtered_function_id, with_xcfg=True)
        pichash_matches_a = client.getMatchesForPicHash(function_entry.pichash, summary=True)
        other_function_entry = client.getFunctionById(other_function_id, with_xcfg=True)
        pichash_matches_b = client.getMatchesForPicHash(other_function_entry.pichash, summary=True)
        node_colors = get_matches_node_colors(filtered_function_id, other_function_id)
        return render_template("result_compare_function_vs.html", entry_a=function_entry, entry_b=other_function_entry, pichash_matches_a=pichash_matches_a, pichash_matches_b=pichash_matches_b, node_colors=json.dumps(node_colors)) 
    # treat family/sample part as if there was no filter
    elif filtered_function_id is not None and client.isFunctionId(filtered_function_id):
        create_match_diagram(current_app, job_info.job_id, matching_result)
        num_families_matched = len(set([sample.family for sample in matching_result.sample_matches]))
        matching_result.filterToFunctionId(filtered_function_id)
        num_functions_matched = len(set([function_match.matched_function_id for function_match in matching_result.function_matches]))
        family_pagination = Pagination(request, num_families_matched, limit=10, query_param="famp")
        function_pagination = Pagination(request, num_functions_matched, query_param="funp")
        return render_template("result_compare_function.html", funid=filtered_function_id, job_info=job_info, famp=family_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider) 
    elif job_info.parameters.startswith("getMatchesForSampleVs"):
        # we need to slice function matches ourselves based on pagination
        function_pagination = Pagination(request, len(matching_result.function_matches), query_param="funp")
        matching_result.function_matches = matching_result.function_matches[function_pagination.start_index:function_pagination.start_index+function_pagination.limit]
        return render_template("result_compare_vs.html", job_info=job_info, matching_result=matching_result, funp=function_pagination, scp=score_color_provider)
    else:
        create_match_diagram(current_app, job_info.job_id, matching_result)
        num_families_matched = len(set([sample.family for sample in matching_result.sample_matches]))
        family_pagination = Pagination(request, num_families_matched, limit=10, query_param="famp")
        function_pagination = Pagination(request, len(matching_result.getAggregatedFunctionMatches()), query_param="funp")
        return render_template("result_compare_all.html", job_info=job_info, famp=family_pagination, funp=function_pagination, matching_result=matching_result, scp=score_color_provider) 


def result_matches_for_cross(job_info, result_json):
    client = McritClient(mcrit_server=get_server_url())
    samples = []
    sample_ids = [int(id) for id in next(iter(result_json.values()))["clustered_sequence"]]
    for sample_id in sample_ids:
        sample_entry = client.getSampleById(sample_id)
        if sample_entry:
            samples.append(sample_entry)
        else:
            reason = f"MCRIT was not able to retrieve information for all samples specified in the original job task. This might be a result of having deleted samples from the database since it was processed. Please consider starting a new job."
            return render_template("result_corrupted.html", reason=reason, matching_result=job_info)
    custom_order = request.args.get('custom','')
    samples_by_method = {}
    sample_indices = {}
    for method, method_results in result_json.items():
        if custom_order:
            order = custom_order.split(',')
        elif "clustered_sequence" in method_results:
            order = method_results["clustered_sequence"]
        else:
            order = None
        ordered_samples = []
        if order:
            for order_sample_id in order:
                for sample in samples:
                    if str(sample.sample_id) == str(order_sample_id):
                        ordered_samples.append(sample)
                        break
                else:
                    reason = f"MCRIT was not able to produce the chosen custom ordering, as some sample_ids are not part of the cross compare originally specified."
                    return render_template("result_corrupted.html", reason=reason, matching_result=result_json)
        if ordered_samples != []:
            samples_by_method[method] = ordered_samples
        else:
            samples_by_method[method] = samples
        sample_indices[method] = [x for index, x in enumerate([sample.sample_id for sample in samples_by_method[method]]) if (index+1) % 5 == 0]
    return render_template('result_cross.html',
        is_corrupted=False,
        samples=samples_by_method,
        sample_indices = sample_indices,
        job_info=job_info,
        sample_to_job_id=get_sample_to_job_id(job_info),
        matching_matches={method: result_json[method]["matching_matches"] for method in result_json.keys()},
        matching_percent={method: result_json[method]["matching_percent"] for method in result_json.keys()},
        score_to_color=score_to_color,
    )


################################################################
# Listing Job information
################################################################

@bp.route('/jobs',methods=('GET', 'POST'))
@mcrit_server_required
@visitor_required
def jobs():
    query = None
    if request.method == 'POST':
        query = request.form['Search']
    # TODO how to get number of jobs?
    client = McritClient(mcrit_server=get_server_url())
    active = request.args.get('active','')
    pagination_others = Pagination(request, client.getJobCount(query), query_param="p_o")
    pagination_vs1 = Pagination(request, client.getJobCount('Vs'), query_param="p_1")
    pagination_vsN = Pagination(request, client.getJobCount('getMatchesForSample'), query_param="p_n")
    pagination_cross = Pagination(request, client.getJobCount('combineMatchesToCross'), query_param="p_c")
    others = client.getQueueData(start=pagination_others.start_index, limit=pagination_others.limit, filter=query)
    # NOTE: the filter is not just 'Vs' anymore. It is longer to prevent false matches. E.g. if a filename contains 'Vs'.
    vs1 = client.getQueueData(start=pagination_vs1.start_index, limit=pagination_vs1.limit, filter='getMatchesForSampleVs(')
    # NOTE: this includes '(', because otherwise vsN would also contain all vs1 jobs.
    vsN = client.getQueueData(start=pagination_vsN.start_index, limit=pagination_vsN.limit, filter='getMatchesForSample(')
    cross = client.getQueueData(start=pagination_vsN.start_index, limit=pagination_vsN.limit, filter='combineMatchesToCross(')
    return render_template('jobs.html', active=active, others=others, cross=cross, vs1=vs1, vsN=vsN, p_o=pagination_others, p_1=pagination_vs1, p_n=pagination_vsN, p_c=pagination_cross, query=query)

@bp.route('/jobs/<job_id>')
@mcrit_server_required
@visitor_required
def job_by_id(job_id):
    auto_refresh = 0
    auto_forward = 0
    client = McritClient(mcrit_server=get_server_url())
    suppress_processing_message = False
    FMT = '%Y-%m-%d-%H:%M:%S'
    try:
        auto_refresh = int(request.args.get("refresh"))
    except TypeError:
        pass
    try:
        auto_forward = int(request.args.get("forward"))
    except TypeError:
        pass

    job_info = client.getJobData(job_id)
    if auto_refresh and job_info and job_info.is_failed:
        auto_refresh = 0
        suppress_processing_message = True
        flash('The job failed!', category='error')

    if job_info is None:
        return render_template("job_invalid.html", job_id=job_id)

    if job_info.finished_at != None:
        if auto_forward:
            if 'addBinarySample' in job_info.parameters:
                suppress_processing_message = True
                flash('Sample submitted successfully!', category='success')
            return redirect(url_for('data.result', job_id=job_id))
    if 'addBinarySample' in job_info.parameters and not suppress_processing_message and auto_refresh:
        flash('We received your sample, currently processing!', category='info')
    child_jobs = sorted([client.getJobData(id) for id in job_info.all_dependencies], key=lambda x: x.number)
    return render_template('job_overview.html', job_info=job_info, auto_refresh=auto_refresh, child_jobs=child_jobs)


@bp.route('/jobs/<job_id>/delete')
@mcrit_server_required
@visitor_required
def delete_job_by_id(job_id):
    client = McritClient(mcrit_server=get_server_url())
    job_data = client.getJobData(job_id)
    raise NotImplementedError("Implement me!")


################################################################
# Binary submission
################################################################

@bp.route('/request_filename_info', methods=['POST'])
@mcrit_server_required
@contributor_required
def request_filename_info():
    try:
        filename = json.loads(request.data)["filename"]
    except Exception:
        filename = ""
    result = {}
    if 'dump' in filename:
        result['dump'] = True
        result['bitness'] = parseBitnessFromFilename(filename)
        base_address = parseBaseAddrFromFilename(filename)
        result['baseaddress'] = "" if not base_address else hex(base_address)
    else:
        result['dump'] = False
    return json.dumps(result), 200


@bp.route('/submit_or_query', methods=('POST',))
@mcrit_server_required
@contributor_required
def submit_or_query():
    form_type = request.form['form_type']
    # NOTE: we do not use redirect to prevent resending of large file data
    if form_type == "query_form":
        return analyze_query()
        # return redirect(url_for("analyze.query"), code=307)
    elif form_type == "submit_form":
        return submit()
        # return redirect(url_for("data.submit"), code=307)


@bp.route('/submit',methods=('GET', 'POST'))
@mcrit_server_required
@contributor_required
def submit():
    client = McritClient(mcrit_server=get_server_url())
    if request.method == 'POST':
        f = request.files.get('file')
        if f is None:
            flash("Please upload a file", category='error')
            return "", 400 # Bad Request
        family = request.form['family']
        version = request.form['version']
        bitness = None
        base_address = None
        is_dump = request.form['options'] == 'dumped'
        if is_dump:
            bitness = int(request.form['bitness'])
            base_address = int(request.form['base_address'], 16)

        binary_content = f.read()
        # check here if it is already part of corpus
        hash = hashlib.sha256(binary_content).hexdigest()
        sample_entry = client.getSampleBySha256(hash)
        if sample_entry is None:
            # NOTE: This flash is done on redirect target
            # flash('We received your sample, currently processing!', category='info')
            job_id = client.addBinarySample(binary_content, filename=f.filename, family=family, version=version, is_dump=is_dump, base_addr=base_address, bitness=bitness)
            return url_for('data.job_by_id', job_id=job_id, refresh=3, forward=1), 202 # Accepted
        else:
            flash('Sample was already in database', category='warning')
            return url_for('explore.sample_by_id', sample_id=sample_entry.sample_id), 202 # Accepted
    all_families = client.getFamilies()
    family_names = [family_entry.family_name for family_entry in all_families.values()]
    return render_template('submit.html', families=family_names, show_submit_fields=True)