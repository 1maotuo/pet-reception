const SAVED_DRAFT_FIELDS = Object.freeze([
  'customer_name', 'contact_phone', 'pet_name', 'pet_species', 'pet_breed',
  'pet_age_years', 'pet_age_months', 'pet_age_text', 'pet_care_notes',
  'need_description', 'preferred_date', 'preferred_time_of_day',
  'staff_preference_text',
  'pet_weight', 'selected_addon_ids',
])

const INTERNAL_DRAFT_FIELDS = Object.freeze([
  'id', 'service_id', 'selected_candidate_id',
  'pending_preparation_id', 'booking_id',
])

const FIELD_LABELS = Object.freeze({
  customer_name: '客户姓名',
  contact_phone: '联系电话',
  pet_name: '宠物名字',
  pet_species: '宠物种类',
  pet_breed: '宠物品种',
  pet_age_years: '宠物年龄',
  pet_age_months: '宠物年龄',
  pet_age_text: '宠物年龄',
  pet_care_notes: '护理注意事项',
  need_description: '服务需求',
  service_id: '服务项目',
  preferred_date: '预约日期',
  preferred_time_of_day: '时间偏好',
  staff_preference_text: '服务人员偏好',
  staff_id: '服务人员',
  selected_candidate_id: '预约时段',
  pet_weight: '宠物体重',
  selected_addon_ids: '附加项目',
})

function hasValue(value) {
  return value !== null && value !== undefined && value !== ''
}

function copyPresent(source, fields) {
  return Object.fromEntries(fields
    .filter((field) => hasValue(source?.[field]))
    .map((field) => [field, source[field]]))
}

function publicMissing(fields = []) {
  return [...new Set(fields.map((field) => (
    field === 'staff_id' ? 'staff_preference_text' : field
  )))]
}

function nextStep(action, details = {}) {
  return { action, type: action, ...details }
}

function publicDraft(draft = {}) {
  const { merchant_id, owner_id, voice_session_id, staff_id, ...safe } = draft
  return safe
}

function currentDraft(result, staffResolution) {
  const draft = result?.draft || {}
  const internalReferences = copyPresent(draft, INTERNAL_DRAFT_FIELDS)
  if (hasValue(internalReferences.id)) {
    internalReferences.draft_id = internalReferences.id
    delete internalReferences.id
  }
  const projected = {
    version: draft.version,
    availability_revision: draft.availability_revision,
    saved_fields: copyPresent(draft, SAVED_DRAFT_FIELDS),
    internal_references: internalReferences,
    missing_for_availability: publicMissing(result?.missing_for_availability),
    missing_for_booking: publicMissing(result?.missing_for_booking),
    ...(result?.service_quote ? { service_quote: result.service_quote } : {}),
  }
  if (hasValue(draft.staff_preference_text)) {
    projected.staff_preference = {
      text: draft.staff_preference_text,
      resolution: staffResolution?.resolution || (draft.staff_id ? 'resolved' : 'unresolved'),
      ...(staffResolution?.display_name
        ? { display_name: staffResolution.display_name }
        : {}),
    }
  }
  return projected
}

function searchMatchesDraft(draftResult, availabilityResult) {
  const draft = draftResult?.draft
  const search = availabilityResult?.search
  const context = availabilityResult?.context
  if (!draft || !search || search.is_current !== true || !context) return false
  const createdAt = Date.parse(search.created_at)
  return String(context.merchant_id) === String(draft.merchant_id)
    && String(context.owner_id) === String(draft.owner_id)
    && String(context.voice_session_id) === String(draft.voice_session_id)
    && String(search.draft_id) === String(draft.id)
    && Number(search.draft_version) === Number(draft.version)
    && Number(search.availability_revision) === Number(draft.availability_revision)
    && Number.isInteger(Number(search.schedule_revision))
    && String(search.service_id) === String(draft.service_id)
    && String(search.preferred_date) === String(draft.preferred_date)
    && String(search.preferred_time_of_day || '') === String(draft.preferred_time_of_day || '')
    && String(search.staff_id || '') === String(draft.staff_id || '')
    && Number.isFinite(createdAt)
}

function availabilityBody(availabilityResult, belongsToCurrentDraft) {
  const {
    draft_id, staff_id, service_id, candidates = [], alternative_dates = [], ...search
  } = availabilityResult?.search || {}
  const publicCandidates = candidates.map((candidate) => {
    const { staff_id: _staffId, service_id: _serviceId, ...safe } = candidate
    return safe
  })
  const publicAlternatives = alternative_dates.map((alternative) => {
    const { available_staff_ids: _staffIds, ...safe } = alternative
    return safe
  })
  if (belongsToCurrentDraft) {
    return { ...search, candidates: publicCandidates, alternative_dates: publicAlternatives }
  }
  return { ...search, candidates: [], alternative_dates: [] }
}

function availabilityState(availabilityResult, belongsToCurrentDraft) {
  const search = availabilityResult?.search || {}
  const state = {
    status: belongsToCurrentDraft ? search.status : 'stale',
    belongs_to_current_draft: belongsToCurrentDraft,
    candidate_count: belongsToCurrentDraft ? (search.candidates || []).length : 0,
  }
  if (belongsToCurrentDraft) {
    state.query = {
      preferred_date: search.preferred_date,
      preferred_time_of_day: search.preferred_time_of_day,
    }
    state.revisions = {
      draft_version: search.draft_version,
      availability_revision: search.availability_revision,
      schedule_revision: search.schedule_revision,
    }
    state.checked_at = search.created_at
    state.internal_references = {
      ...(hasValue(search.service_id) ? { service_id: search.service_id } : {}),
    }
  }
  return state
}

function availabilityNextStep(search, belongsToCurrentDraft) {
  if (!belongsToCurrentDraft) {
    return nextStep('search_availability', { for_goal: 'search_availability' })
  }
  if (search.status === 'no_availability' && search.alternative_dates?.length) {
    return nextStep('present_alternative_dates', {
      for_goal: 'search_availability',
      instruction: '只能说明返回的替代日期；客户选定后先更新日期并重新查询。',
    })
  }
  if (search.candidates?.length) {
    return nextStep('present_availability', {
      for_goal: 'search_availability',
      instruction: '用客户听得懂的当地时间回答，不朗读任何内部引用。',
    })
  }
  return nextStep('explain_no_availability', {
    for_goal: 'search_availability',
    instruction: '只说本次真实查询没有可选时段，不猜测原因。',
  })
}

function availabilityView(draftResult, availabilityResult, staffResolution) {
  const belongsToCurrentDraft = searchMatchesDraft(draftResult, availabilityResult)
  const availability = availabilityBody(availabilityResult, belongsToCurrentDraft)
  return {
    belongsToCurrentDraft,
    availability,
    current_state: {
      draft: currentDraft(draftResult, staffResolution),
      availability: availabilityState(availabilityResult, belongsToCurrentDraft),
    },
    next_step: availabilityNextStep(availability, belongsToCurrentDraft),
  }
}

function goalFields(result, currentGoal) {
  const missingForCurrentGoal = currentGoal === 'search_availability'
    ? publicMissing(result?.missing_for_availability)
    : []
  return {
    current_goal: currentGoal,
    ready_for_current_goal: missingForCurrentGoal.length === 0,
    missing_for_current_goal: missingForCurrentGoal,
    missing_for_booking_later: publicMissing(result?.missing_for_booking),
  }
}

function askForCurrentGoal(missing, currentGoal) {
  return nextStep('ask_one_field', {
    field: missing[0],
    for_goal: currentGoal,
  })
}

export function projectAvailabilityReceipt(draftResult, availabilityResult, {
  staffResolution,
} = {}) {
  const view = availabilityView(draftResult, availabilityResult, staffResolution)
  return {
    status: view.belongsToCurrentDraft ? 'ok' : 'stale',
    ...goalFields(draftResult, 'search_availability'),
    current_state: view.current_state,
    effect: {
      type: view.belongsToCurrentDraft ? 'availability_checked' : 'stale_availability_ignored',
      applied: true,
    },
    next_step: view.next_step,
    availability: view.availability,
  }
}

export function projectDraftReceipt(result, {
  searchRequested = false,
  availabilityResult,
  availabilityError,
  staffResolution,
} = {}) {
  const changedFields = [...(result?.changed_fields || [])]
  const draft = publicDraft(result?.draft)
  const unresolvedStaff = staffResolution?.resolution === 'unresolved'
  const currentGoal = searchRequested
    ? 'search_availability'
    : unresolvedStaff
      ? 'resolve_staff_preference'
      : 'update_booking_details'
  const goals = unresolvedStaff && !searchRequested
    ? {
        current_goal: currentGoal,
        ready_for_current_goal: false,
        missing_for_current_goal: ['staff_preference_text'],
        missing_for_booking_later: publicMissing(result?.missing_for_booking),
      }
    : goalFields(result, currentGoal)
  const base = {
    status: 'ok',
    ...goals,
    current_state: { draft: currentDraft(result, staffResolution) },
    effect: {
      type: changedFields.length ? 'draft_updated' : 'draft_already_current',
      applied: true,
      changed_fields: changedFields,
    },
    next_step: unresolvedStaff && !searchRequested
      ? askForCurrentGoal(['staff_preference_text'], 'resolve_staff_preference')
      : searchRequested
      ? nextStep('search_availability', { for_goal: 'search_availability' })
      : nextStep('answer_or_ask_for_customer_goal'),
    draft,
    changed_fields: changedFields,
    missing_for_availability: publicMissing(result?.missing_for_availability),
    missing_for_booking: publicMissing(result?.missing_for_booking),
  }

  if (!searchRequested) return base
  if (result?.missing_for_availability?.length) {
    base.next_step = askForCurrentGoal(
      publicMissing(result.missing_for_availability),
      'search_availability',
    )
    return base
  }
  if (availabilityError) {
    return {
      ...base,
      status: 'partial_success',
      effect: {
        ...base.effect,
        type: 'draft_saved_availability_failed',
        availability_checked: false,
      },
      next_step: nextStep('retry_availability', {
        for_goal: 'search_availability',
        reason: 'temporarily_unavailable',
      }),
    }
  }
  if (availabilityResult) {
    const view = availabilityView(result, availabilityResult, staffResolution)
    return {
      ...base,
      status: view.belongsToCurrentDraft ? 'ok' : 'stale',
      current_state: view.current_state,
      effect: {
        ...base.effect,
        type: changedFields.length
          ? 'draft_updated_and_availability_checked'
          : 'draft_already_current_and_availability_checked',
        availability_checked: view.belongsToCurrentDraft,
      },
      next_step: view.next_step,
      availability: view.availability,
    }
  }
  return base
}

function invalidFieldNames(error) {
  const details = Array.isArray(error?.detail)
    ? error.detail
    : Array.isArray(error?.detail?.errors)
      ? error.detail.errors
      : []
  const raw = [
    ...(error?.detail?.invalid_fields || []),
    ...details.map((item) => item?.loc?.at?.(-1)).filter(Boolean),
  ]
  return [...new Set(raw.map((field) => FIELD_LABELS[field] || '预约资料'))]
}

function rejectionReason(error) {
  const code = String(error?.code || error?.detail?.code || '')
  if (code === 'needs_input') return 'missing_information'
  if (code.includes('past')) return 'date_not_available'
  if (code.includes('staff')) return 'staff_not_available'
  if (code.includes('service')) return 'service_not_available'
  if (error?.status === 409) return 'current_state_changed'
  if (error?.status === 422) return 'invalid_value'
  return 'business_rule_rejected'
}

export function projectRejectedReceipt(toolName, error, draftResult) {
  const isUpdate = toolName === 'update_booking_draft'
  const invalidFields = invalidFieldNames(error)
  const missing = [...(error?.detail?.missing_fields || [])]
  const current_state = error?.status !== 409 && draftResult?.draft
    ? { draft: currentDraft(draftResult) }
    : {}
  const reason = rejectionReason(error)
  const nextStepValue = reason === 'current_state_changed'
    ? nextStep(isUpdate ? 'retry_update_once' : 'search_availability', {
        instruction: '下一次调用会先读取最新草稿；同一状态不得循环重试。',
      })
    : missing.length
    ? askForCurrentGoal(publicMissing(missing), 'search_availability')
    : invalidFields.length
      ? nextStep('clarify_field', { fields: invalidFields })
      : nextStep('explain_rejection')
  return {
    status: isUpdate ? 'update_rejected' : 'business_rule_rejected',
    current_goal: missing.length ? 'search_availability' : 'update_booking_details',
    ready_for_current_goal: false,
    missing_for_current_goal: publicMissing(missing),
    missing_for_booking_later: publicMissing(draftResult?.missing_for_booking),
    current_state,
    effect: {
      type: isUpdate ? 'draft_update_rejected' : 'operation_rejected',
      applied: false,
    },
    invalid_fields: invalidFields,
    reason,
    next_step: nextStepValue,
  }
}

export function projectOperationReceipt(result) {
  const current_state = {}
  if (result?.preparation) {
    current_state.preparation = {
      status: result.preparation.status || result.status,
      draft_version: result.preparation.draft_version,
      internal_reference: { preparation_id: result.preparation.id },
    }
  }
  if (result?.booking) {
    current_state.booking = {
      status: result.booking.status || result.status,
      ...(result.booking.confirmation_code
        ? { confirmation_code: result.booking.confirmation_code }
        : {}),
      internal_reference: { booking_id: result.booking.id },
    }
  }
  const receipt = {
    operation_id: result.operation_id,
    status: result.status,
    current_state,
    effect: {
      type: result.status === 'confirmed'
        ? 'booking_confirmed'
        : result.status === 'awaiting_payment'
          ? 'booking_submitted_awaiting_payment'
          : result.status === 'prepared'
            ? 'booking_prepared'
            : 'booking_state_read',
      applied: true,
    },
    next_step: result.status === 'prepared'
      ? nextStep('present_controlled_summary')
      : result.status === 'awaiting_payment'
        ? nextStep('complete_payment')
        : result.status === 'confirmed'
          ? nextStep('inform_confirmed')
          : nextStep('follow_backend_status'),
    preparation: result.preparation,
    booking: result.booking,
  }
  if (result.customer_credential) {
    receipt.customer_action = {
      payment_required: result.status === 'awaiting_payment',
      booking_reference: result.customer_credential.booking_reference,
      customer_page_available: true,
      security_note: '访问令牌和链接只交给受控界面，不提供给模型，也不要朗读。',
    }
  }
  return receipt
}
