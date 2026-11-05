module signal_processing_isp_bayer_oecf #(
    parameter int BPP = 12,
    parameter int LUT_DATA_W = 16
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [BPP-1:0]        in_sample,
    input  logic [1:0]            in_x_lsb,
    input  logic [1:0]            in_y_lsb,
    input  logic [1:0]            in_bayer_pattern,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [BPP-1:0]        out_sample,

    output logic                  r_lut_req_valid,
    output logic [BPP-1:0]        r_lut_req_addr,
    input  logic                  r_lut_req_ready,
    input  logic                  r_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] r_lut_resp_rdata,

    output logic                  gr_lut_req_valid,
    output logic [BPP-1:0]        gr_lut_req_addr,
    input  logic                  gr_lut_req_ready,
    input  logic                  gr_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] gr_lut_resp_rdata,

    output logic                  gb_lut_req_valid,
    output logic [BPP-1:0]        gb_lut_req_addr,
    input  logic                  gb_lut_req_ready,
    input  logic                  gb_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] gb_lut_resp_rdata,

    output logic                  b_lut_req_valid,
    output logic [BPP-1:0]        b_lut_req_addr,
    input  logic                  b_lut_req_ready,
    input  logic                  b_lut_resp_valid,
    input  logic [LUT_DATA_W-1:0] b_lut_resp_rdata
);

    // One global in-flight read preserves order even if different LUT planes
    // have different latencies. Four response credits cover a blocked output.
    logic [BPP-1:0] results[0:3];
    logic [1:0] head, tail, input_plane, select_plane, pending_plane, request_plane;
    logic [BPP-1:0] request_address, selected_address;
    logic [2:0] count;
    logic pending, request_full, pop_result, push_result, allow_request, issue_request;
    logic selected_ready, response_valid;
    logic [LUT_DATA_W-1:0] response_data;
    logic [BPP-1:0] converted;
    assign out_valid = count != 0;
    assign out_sample = out_valid ? results[head] : '0;
    assign pop_result = out_valid && out_ready;
    assign push_result = pending && response_valid;
    // A reserved response consumes a credit before it reaches the FIFO.
    assign allow_request = !rst && (!pending || response_valid) &&
                           (int'(count) + int'(pending) - int'(pop_result) < 4);
    assign issue_request = (request_full || in_valid) && allow_request && selected_ready;
    assign in_ready = !request_full || issue_request;
    assign select_plane = request_full ? request_plane : input_plane;
    assign selected_address = request_full ? request_address : in_sample;
    always_comb begin
        case(in_bayer_pattern)
            0:input_plane={in_y_lsb[0],in_x_lsb[0]};
            1:input_plane={in_y_lsb[0],in_x_lsb[0]} ^ 2'b11;
            2:input_plane={in_y_lsb[0],in_x_lsb[0]} ^ 2'b01;
            default:input_plane={in_y_lsb[0],in_x_lsb[0]} ^ 2'b10;
        endcase
        case(select_plane)
            0:selected_ready=r_lut_req_ready;
            1:selected_ready=gr_lut_req_ready;
            2:selected_ready=gb_lut_req_ready;
            default:selected_ready=b_lut_req_ready;
        endcase
        case(pending_plane)
            0:begin response_valid=r_lut_resp_valid; response_data=r_lut_resp_rdata; end
            1:begin response_valid=gr_lut_resp_valid; response_data=gr_lut_resp_rdata; end
            2:begin response_valid=gb_lut_resp_valid; response_data=gb_lut_resp_rdata; end
            default:begin response_valid=b_lut_resp_valid; response_data=b_lut_resp_rdata; end
        endcase
    end
    assign converted = response_data > LUT_DATA_W'({BPP{1'b1}}) ? {BPP{1'b1}} : response_data[BPP-1:0];
    assign r_lut_req_valid = (request_full || in_valid) && allow_request && select_plane == 2'd0;
    assign r_lut_req_addr = selected_address;
    assign gr_lut_req_valid = (request_full || in_valid) && allow_request && select_plane == 2'd1;
    assign gr_lut_req_addr = selected_address;
    assign gb_lut_req_valid = (request_full || in_valid) && allow_request && select_plane == 2'd2;
    assign gb_lut_req_addr = selected_address;
    assign b_lut_req_valid = (request_full || in_valid) && allow_request && select_plane == 2'd3;
    assign b_lut_req_addr = selected_address;
    always_ff @(posedge clk) begin
        if(rst) begin
            head<=0; tail<=0; count<=0; pending<=0; pending_plane<=0;
            request_full<=0; request_address<=0; request_plane<=0;
            for(int i=0;i<4;i++) results[i]<='0;
        end else begin
            case({push_result,pop_result})
                2'b10:count<=count+1'b1;
                2'b01:count<=count-1'b1;
                default:count<=count;
            endcase
            if(pop_result) head<=head+1'b1;
            if(push_result) begin results[tail]<=converted; tail<=tail+1'b1; end
            if(push_result) pending<=0;
            if(issue_request) begin pending<=1; pending_plane<=select_plane; end
            // The request skid buffer accepts the first input even when memory
            // is stalled, and holds that exact request until it transfers.
            if(in_ready) begin
                request_full <= in_valid && (request_full || !issue_request);
                if(in_valid) begin request_address<=in_sample; request_plane<=input_plane; end
            end
        end
    end
endmodule
