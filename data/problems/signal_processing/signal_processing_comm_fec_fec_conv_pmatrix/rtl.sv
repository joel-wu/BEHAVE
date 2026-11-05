module signal_processing_comm_fec_fec_conv_pmatrix (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [0:0] in_k_sel,
    input  logic [2:0] in_rate_sel,
    input  logic [3:0] in_index,

    output logic       out_valid,
    input  logic       out_ready,
    output logic       out_found,
    output logic [0:0] out_bit
);

    logic busy;
    logic next_found;
    logic next_bit;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        next_found = 1'b0;
        next_bit   = 1'b0;

        unique case ({in_k_sel, in_rate_sel})
            4'b0_000: begin
                if (in_index < 4) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b1;
                        4'd3: next_bit = 1'b0;
                    endcase
                end
            end

            4'b0_001: begin
                if (in_index < 6) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b0;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b0;
                        4'd5: next_bit = 1'b1;
                    endcase
                end
            end

            4'b0_010: begin
                if (in_index < 8) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b1;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b1;
                        4'd5: next_bit = 1'b0;
                        4'd6: next_bit = 1'b0;
                        4'd7: next_bit = 1'b0;
                    endcase
                end
            end

            4'b0_011: begin
                if (in_index < 10) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b0;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b0;
                        4'd5: next_bit = 1'b1;
                        4'd6: next_bit = 1'b0;
                        4'd7: next_bit = 1'b1;
                        4'd8: next_bit = 1'b0;
                        4'd9: next_bit = 1'b1;
                    endcase
                end
            end

            4'b0_100: begin
                if (in_index < 12) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0:  next_bit = 1'b1;
                        4'd1:  next_bit = 1'b1;
                        4'd2:  next_bit = 1'b1;
                        4'd3:  next_bit = 1'b0;
                        4'd4:  next_bit = 1'b1;
                        4'd5:  next_bit = 1'b0;
                        4'd6:  next_bit = 1'b1;
                        4'd7:  next_bit = 1'b0;
                        4'd8:  next_bit = 1'b0;
                        4'd9:  next_bit = 1'b1;
                        4'd10: next_bit = 1'b0;
                        4'd11: next_bit = 1'b1;
                    endcase
                end
            end

            4'b0_101: begin
                if (in_index < 14) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0:  next_bit = 1'b1;
                        4'd1:  next_bit = 1'b1;
                        4'd2:  next_bit = 1'b1;
                        4'd3:  next_bit = 1'b1;
                        4'd4:  next_bit = 1'b0;
                        4'd5:  next_bit = 1'b1;
                        4'd6:  next_bit = 1'b0;
                        4'd7:  next_bit = 1'b1;
                        4'd8:  next_bit = 1'b0;
                        4'd9:  next_bit = 1'b0;
                        4'd10: next_bit = 1'b0;
                        4'd11: next_bit = 1'b1;
                        4'd12: next_bit = 1'b0;
                        4'd13: next_bit = 1'b1;
                    endcase
                end
            end

            4'b1_000: begin
                if (in_index < 4) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b1;
                        4'd3: next_bit = 1'b0;
                    endcase
                end
            end

            4'b1_001: begin
                if (in_index < 6) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b1;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b0;
                        4'd5: next_bit = 1'b0;
                    endcase
                end
            end

            4'b1_010: begin
                if (in_index < 8) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b1;
                        4'd2: next_bit = 1'b0;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b1;
                        4'd5: next_bit = 1'b0;
                        4'd6: next_bit = 1'b1;
                        4'd7: next_bit = 1'b0;
                    endcase
                end
            end

            4'b1_011: begin
                if (in_index < 10) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0: next_bit = 1'b1;
                        4'd1: next_bit = 1'b0;
                        4'd2: next_bit = 1'b1;
                        4'd3: next_bit = 1'b1;
                        4'd4: next_bit = 1'b0;
                        4'd5: next_bit = 1'b1;
                        4'd6: next_bit = 1'b1;
                        4'd7: next_bit = 1'b0;
                        4'd8: next_bit = 1'b0;
                        4'd9: next_bit = 1'b1;
                    endcase
                end
            end

            4'b1_100: begin
                if (in_index < 12) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0:  next_bit = 1'b1;
                        4'd1:  next_bit = 1'b1;
                        4'd2:  next_bit = 1'b0;
                        4'd3:  next_bit = 1'b1;
                        4'd4:  next_bit = 1'b1;
                        4'd5:  next_bit = 1'b0;
                        4'd6:  next_bit = 1'b1;
                        4'd7:  next_bit = 1'b0;
                        4'd8:  next_bit = 1'b1;
                        4'd9:  next_bit = 1'b0;
                        4'd10: next_bit = 1'b0;
                        4'd11: next_bit = 1'b1;
                    endcase
                end
            end

            4'b1_101: begin
                if (in_index < 14) begin
                    next_found = 1'b1;
                    unique case (in_index)
                        4'd0:  next_bit = 1'b1;
                        4'd1:  next_bit = 1'b1;
                        4'd2:  next_bit = 1'b0;
                        4'd3:  next_bit = 1'b1;
                        4'd4:  next_bit = 1'b0;
                        4'd5:  next_bit = 1'b1;
                        4'd6:  next_bit = 1'b1;
                        4'd7:  next_bit = 1'b1;
                        4'd8:  next_bit = 1'b0;
                        4'd9:  next_bit = 1'b1;
                        4'd10: next_bit = 1'b0;
                        4'd11: next_bit = 1'b1;
                        4'd12: next_bit = 1'b0;
                        4'd13: next_bit = 1'b0;
                    endcase
                end
            end

            default: begin
                next_found = 1'b0;
                next_bit   = 1'b0;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_found <= 1'b0;
            out_bit   <= 1'b0;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_found <= next_found;
            out_bit   <= next_bit;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule