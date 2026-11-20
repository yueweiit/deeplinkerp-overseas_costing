"""Batch every selected image and keep unfinished transcription visible."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


def describe_images(documents, describe_batch, *, max_images, max_bytes, ocr_image=None):
    images, owners = [], {}
    for document in documents or []:
        document_id = str(document.get('document_id') or '')
        selected = [image for image in document.get('_image_payloads') or [] if image.get('data_url')]
        if not selected:
            continue
        owners[document_id] = document
        document['image_read_status'] = {'prepared': len(selected), 'processed': 0, 'pending': len(selected),
                                         'vision_processed': 0, 'ocr_processed': 0,
                                         'pending_anchors': [image.get('anchor') or {} for image in selected]}
        images.extend({**image, 'document_id': document_id} for image in selected)
    result = {'ok': True, 'model': '', 'observations': [], 'warning': ''}
    batch, size = [], 0
    for image in images:
        image_size = int(image.get('size_bytes') or len(image['data_url'].partition(',')[2]) * 3 // 4)
        if image_size > max_bytes:
            owners[image['document_id']].setdefault('read_issues', []).append({
                'code': 'SOURCE_IMAGE_SIZE_LIMIT', 'hint': 'A workbook image exceeds the vision size limit.',
                'anchor': image.get('anchor') or {}})
            continue
        if batch and (len(batch) >= max_images or size + image_size > max_bytes):
            _describe_batch(batch, describe_batch, owners, result, ocr_image)
            batch, size = [], 0
        batch.append(image)
        size += image_size
    if batch:
        _describe_batch(batch, describe_batch, owners, result, ocr_image)
    return result


def _describe_batch(images, describe_batch, owners, result, ocr_image):
    batch = describe_batch(images)
    result['model'] = batch.get('model') or result['model']
    result['warning'] = '；'.join(dict.fromkeys(
        value for value in (result['warning'], batch.get('warning')) if value))
    observations = [{**row, 'method': 'vision'} for row in batch.get('observations') or []]
    for image in images:
        matched = _matching_observations(image, observations)
        if matched:
            _record_image(image, matched, owners, result)
    if ocr_image:
        _ocr_missing_images(images, observations, owners, result, ocr_image)
    complete = all(_matching_observations(image, observations) for image in images)
    result['ok'] = result['ok'] and (complete if ocr_image else batch.get('ok') is True)


def _record_image(image, observations, owners, result):
    document = owners[image['document_id']]
    document.setdefault('vision_observations', []).extend(observations)
    result['observations'].extend(observations)
    status = document['image_read_status']
    status['processed'] += 1
    method = 'ocr' if observations[0].get('method') == 'ocr' else 'vision'
    status[method + '_processed'] += 1
    status['pending'] -= 1
    status['pending_anchors'].remove(image.get('anchor') or {})


def _matching_observations(image, observations):
    return [row for row in observations if str(row.get('document_id') or '') == image['document_id']
            and row.get('anchor') == image.get('anchor') and str(row.get('description') or '').strip()]


def _ocr_missing_images(images, observations, owners, result, ocr_image):
    for image in images:
        if _matching_observations(image, observations):
            continue
        text = str(ocr_image(image) or '').strip()
        if text:
            observation = {'document_id': image['document_id'], 'anchor': image.get('anchor') or {},
                           'description': text, 'method': 'ocr'}
            observations.append(observation)
            _record_image(image, [observation], owners, result)
        else:
            owners[image['document_id']].setdefault('read_issues', []).append({
                'code': 'SOURCE_IMAGE_OCR_UNREAD', 'hint': _('A workbook image could not be transcribed by OCR.'),
                'anchor': image.get('anchor') or {}})


def document_read_issues(document):
    issues = list(document.get('read_issues') or [])
    status = document.get('image_read_status') or {}
    if status.get('pending'):
        issues.append({'code': 'SOURCE_IMAGES_UNREAD', 'hint': _(
            'Some workbook images have not been transcribed ({0} remaining).').format(status['pending']),
            'pending_count': status['pending'], 'anchors': status.get('pending_anchors') or []})
    source = document.get('source_ref') or {}
    return [{**row, 'file_name': source.get('file') or source.get('label') or '',
             'source_id': str(source.get('source_id') or ''),
             'document_id': str(document.get('document_id') or '')} for row in issues]
