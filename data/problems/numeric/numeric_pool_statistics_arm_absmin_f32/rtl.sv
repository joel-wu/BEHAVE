module absmin_f32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_base_addr,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_result,
    output logic [1:0]  out_index,

    output logic        src_req_valid,
    output logic [1:0]  src_req_addr,
    input  logic        src_req_ready,
    input  logic        src_resp_valid,
    input  logic [31:0] src_resp_rdata
);

logic        busy;
logic [1:0]  base_addr_reg;
logic [2:0]  issued_count;
logic [2:0]  received_count;
logic [31:0] best_value;
logic [1:0]  best_index;
logic        best_is_nan;

wire output_consumed;
wire input_accepted;
wire done;

assign done = (received_count == 3'd4);
assign out_valid = busy & done;
assign in_ready = ~busy | output_consumed;
assign output_consumed = out_valid & out_ready;
assign input_accepted = in_valid & in_ready;

function automatic logic is_nan_f32_abs(input logic [31:0] v);
    begin
        if ((v[30:23] == 8'hFF) && (v[22:0] != 23'd0))
            is_nan_f32_abs = 1'b1;
        else
            is_nan_f32_abs = 1'b0;
    end
endfunction

always_ff @(posedge clk) begin
    logic [31:0] abs_value;
    logic        cand_is_nan;
    logic [1:0]  cand_index;
    logic        take_cand;
    logic [1:0]  req_addr_next;

    if (rst) begin
        busy           <= 1'b0;
        base_addr_reg  <= 2'b00;
        issued_count   <= 3'd0;
        received_count <= 3'd0;
        best_value     <= 32'h0000_0000;
        best_index     <= 2'b00;
        best_is_nan    <= 1'b0;
        out_result     <= 32'h0000_0000;
        out_index      <= 2'b00;
        src_req_valid  <= 1'b0;
        src_req_addr   <= 2'b00;
    end else begin
        if (input_accepted) begin
            busy           <= 1'b1;
            base_addr_reg  <= in_base_addr;
            issued_count   <= 3'd0;
            received_count <= 3'd0;
            best_value     <= 32'h0000_0000;
            best_index     <= 2'b00;
            best_is_nan    <= 1'b0;
            out_result     <= out_result;
            out_index      <= out_index;
            src_req_valid  <= 1'b1;
            src_req_addr   <= in_base_addr;
        end else begin
            if (src_req_valid && src_req_ready) begin
                if (issued_count == 3'd3) begin
                    src_req_valid <= 1'b0;
                    src_req_addr  <= src_req_addr;
                end else begin
                    req_addr_next = base_addr_reg + issued_count[1:0] + 2'd1;
                    src_req_valid <= 1'b1;
                    src_req_addr  <= req_addr_next;
                end
                issued_count <= issued_count + 3'd1;
            end

            if (src_resp_valid && busy) begin
                abs_value   = src_resp_rdata & 32'h7FFF_FFFF;
                cand_is_nan = is_nan_f32_abs(abs_value);
                cand_index  = received_count[1:0];

                if (received_count == 3'd0) begin
                    best_value  <= abs_value;
                    best_index  <= 2'd0;
                    best_is_nan <= cand_is_nan;
                end else begin
                    take_cand = (~cand_is_nan) & (~best_is_nan) & (abs_value < best_value);
                    if (take_cand) begin
                        best_value  <= abs_value;
                        best_index  <= cand_index;
                        best_is_nan <= 1'b0;
                    end

                    if (received_count == 3'd3) begin
                        if (take_cand) begin
                            out_result <= abs_value;
                            out_index  <= cand_index;
                        end else begin
                            out_result <= best_value;
                            out_index  <= best_index;
                        end
                    end
                end

                received_count <= received_count + 3'd1;
            end

            if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end
end

endmodule
