// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

use vmm::rpc_interface::VmmAction;

use super::super::parsed_request::{ParsedRequest, RequestError};

pub(crate) fn parse_put_attach() -> Result<ParsedRequest, RequestError> {
    Ok(ParsedRequest::new_sync(VmmAction::Attach))
}
