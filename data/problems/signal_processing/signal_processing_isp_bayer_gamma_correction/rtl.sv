module signal_processing_isp_bayer_gamma_correction #(
    parameter int BIT_DEPTH = 10
) (
    input  logic                 clk,
    input  logic                 rst,

    input  logic                 in_valid,
    output logic                 in_ready,
    input  logic [BIT_DEPTH-1:0] in_pixel,

    output logic                 out_valid,
    input  logic                 out_ready,
    output logic [BIT_DEPTH-1:0] out_pixel,

    output logic                 gamma_lut_req_valid,
    output logic [BIT_DEPTH-1:0] gamma_lut_req_addr,
    input  logic                 gamma_lut_req_ready,
    input  logic                 gamma_lut_resp_valid,
    input  logic [BIT_DEPTH-1:0] gamma_lut_resp_rdata
);

    // One global in-flight read preserves order even if different LUT planes
    // have different latencies. Four response credits cover a blocked output.
    logic [BIT_DEPTH-1:0] results[0:3];
    logic [1:0] head, tail, input_plane, select_plane, pending_plane, request_plane;
    logic [BIT_DEPTH-1:0] request_address, selected_address;
    logic [2:0] count;
    logic pending, request_full, pop_result, push_result, allow_request, issue_request;
    logic selected_ready, response_valid;
    logic [BIT_DEPTH-1:0] response_data;
    logic [BIT_DEPTH-1:0] converted;
    assign out_valid = count != 0;
    assign out_pixel = out_valid ? results[head] : '0;
    assign pop_result = out_valid && out_ready;
    assign push_result = pending && response_valid;
    // A reserved response consumes a credit before it reaches the FIFO.
    assign allow_request = !rst && (!pending || response_valid) &&
                           (int'(count) + int'(pending) - int'(pop_result) < 4);
    assign issue_request = (request_full || in_valid) && allow_request && selected_ready;
    assign in_ready = !request_full || issue_request;
    assign select_plane = request_full ? request_plane : input_plane;
    assign selected_address = request_full ? request_address : in_pixel;
    assign input_plane = 0;
    assign selected_ready = gamma_lut_req_ready;
    assign response_valid = gamma_lut_resp_valid;
    assign response_data = gamma_lut_resp_rdata;
    assign converted = response_data;
    assign gamma_lut_req_valid = (request_full || in_valid) && allow_request && select_plane == 2'd0;
    assign gamma_lut_req_addr = selected_address;
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
                if(in_valid) begin request_address<=in_pixel; request_plane<=input_plane; end
            end
        end
    end
endmodule
