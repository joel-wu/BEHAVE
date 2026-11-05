module EncoderIndexLifecycle (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [1:0] in_op,
    input logic in_use_index,
    input logic in_lockin_only,
    input logic in_override_enable,
    input logic in_use_index_offset,
    input logic in_pre_calibrated,
    input logic in_anticogging_pre_calibrated,
    input logic signed [31:0] in_offset_count,
    input logic signed [31:0] in_circular_count,
    input logic signed [31:0] in_linear_count,
    output logic out_valid,
    input logic out_ready,
    output logic signed [31:0] out_circular_count,
    output logic signed [31:0] out_linear_count,
    output logic out_is_ready,
    output logic out_index_found,
    output logic out_subscribed,
    output logic out_anticogging_valid
);
    logic busy;
    assign in_ready = !rst && (!busy || out_ready);
    assign out_valid = !rst && busy;
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            out_circular_count <= 32'd0;
            out_linear_count <= 32'd0;
            out_is_ready <= 1'b0;
            out_index_found <= 1'b0;
            out_subscribed <= 1'b0;
            out_anticogging_valid <= 1'b0;
        end else if (in_valid && in_ready) begin
            busy <= 1'b1;
            case (in_op)
                2'd1: out_subscribed <= in_use_index && (in_override_enable || !in_lockin_only);
                2'd2: begin
                    out_circular_count <= in_circular_count;
                    out_linear_count <= in_linear_count;
                end
                2'd3: if (out_subscribed) begin
                    if (in_use_index) begin
                        out_circular_count <= 32'd0;
                        if (in_use_index_offset) out_linear_count <= in_offset_count;
                        out_is_ready <= in_pre_calibrated;
                        if (in_pre_calibrated && in_anticogging_pre_calibrated)
                            out_anticogging_valid <= 1'b1;
                        out_index_found <= 1'b1;
                    end
                    out_subscribed <= 1'b0;
                end
                default: begin end
            endcase
        end else if (out_ready) busy <= 1'b0;
    end
endmodule
