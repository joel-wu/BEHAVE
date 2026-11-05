module ai_ml_tx_pos_other_alibi_bias (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_attention_mask,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [2:0]   out_head_index,
    output logic [511:0] out_alibi
);

    logic        busy;
    logic [2:0]  head_idx;
    logic [3:0]  position [0:15];
    logic [511:0] out_alibi_next;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (head_idx == 3'd7);

    assign in_ready       = ~busy | frame_complete;
    assign out_valid      = busy;
    assign out_head_index = busy ? head_idx : 3'b000;
    assign out_alibi      = busy ? out_alibi_next : 512'b0;

    function automatic logic [31:0] table_lookup(
        input logic [2:0] head,
        input logic [3:0] pos
    );
        begin
            table_lookup = 32'h00000000;
            unique case (head)
                3'd0: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3F000000;
                        4'd2:  table_lookup = 32'h3F800000;
                        4'd3:  table_lookup = 32'h3FC00000;
                        4'd4:  table_lookup = 32'h40000000;
                        4'd5:  table_lookup = 32'h40200000;
                        4'd6:  table_lookup = 32'h40400000;
                        4'd7:  table_lookup = 32'h40600000;
                        4'd8:  table_lookup = 32'h40800000;
                        4'd9:  table_lookup = 32'h40900000;
                        4'd10: table_lookup = 32'h40A00000;
                        4'd11: table_lookup = 32'h40B00000;
                        4'd12: table_lookup = 32'h40C00000;
                        4'd13: table_lookup = 32'h40D00000;
                        4'd14: table_lookup = 32'h40E00000;
                        4'd15: table_lookup = 32'h40F00000;
                    endcase
                end
                3'd1: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3E800000;
                        4'd2:  table_lookup = 32'h3F000000;
                        4'd3:  table_lookup = 32'h3F400000;
                        4'd4:  table_lookup = 32'h3F800000;
                        4'd5:  table_lookup = 32'h3FA00000;
                        4'd6:  table_lookup = 32'h3FC00000;
                        4'd7:  table_lookup = 32'h3FE00000;
                        4'd8:  table_lookup = 32'h40000000;
                        4'd9:  table_lookup = 32'h40100000;
                        4'd10: table_lookup = 32'h40200000;
                        4'd11: table_lookup = 32'h40300000;
                        4'd12: table_lookup = 32'h40400000;
                        4'd13: table_lookup = 32'h40500000;
                        4'd14: table_lookup = 32'h40600000;
                        4'd15: table_lookup = 32'h40700000;
                    endcase
                end
                3'd2: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3E000000;
                        4'd2:  table_lookup = 32'h3E800000;
                        4'd3:  table_lookup = 32'h3EC00000;
                        4'd4:  table_lookup = 32'h3F000000;
                        4'd5:  table_lookup = 32'h3F200000;
                        4'd6:  table_lookup = 32'h3F400000;
                        4'd7:  table_lookup = 32'h3F600000;
                        4'd8:  table_lookup = 32'h3F800000;
                        4'd9:  table_lookup = 32'h3F900000;
                        4'd10: table_lookup = 32'h3FA00000;
                        4'd11: table_lookup = 32'h3FB00000;
                        4'd12: table_lookup = 32'h3FC00000;
                        4'd13: table_lookup = 32'h3FD00000;
                        4'd14: table_lookup = 32'h3FE00000;
                        4'd15: table_lookup = 32'h3FF00000;
                    endcase
                end
                3'd3: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3D800000;
                        4'd2:  table_lookup = 32'h3E000000;
                        4'd3:  table_lookup = 32'h3E400000;
                        4'd4:  table_lookup = 32'h3E800000;
                        4'd5:  table_lookup = 32'h3EA00000;
                        4'd6:  table_lookup = 32'h3EC00000;
                        4'd7:  table_lookup = 32'h3EE00000;
                        4'd8:  table_lookup = 32'h3F000000;
                        4'd9:  table_lookup = 32'h3F100000;
                        4'd10: table_lookup = 32'h3F200000;
                        4'd11: table_lookup = 32'h3F300000;
                        4'd12: table_lookup = 32'h3F400000;
                        4'd13: table_lookup = 32'h3F500000;
                        4'd14: table_lookup = 32'h3F600000;
                        4'd15: table_lookup = 32'h3F700000;
                    endcase
                end
                3'd4: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3D000000;
                        4'd2:  table_lookup = 32'h3D800000;
                        4'd3:  table_lookup = 32'h3DC00000;
                        4'd4:  table_lookup = 32'h3E000000;
                        4'd5:  table_lookup = 32'h3E200000;
                        4'd6:  table_lookup = 32'h3E400000;
                        4'd7:  table_lookup = 32'h3E600000;
                        4'd8:  table_lookup = 32'h3E800000;
                        4'd9:  table_lookup = 32'h3E900000;
                        4'd10: table_lookup = 32'h3EA00000;
                        4'd11: table_lookup = 32'h3EB00000;
                        4'd12: table_lookup = 32'h3EC00000;
                        4'd13: table_lookup = 32'h3ED00000;
                        4'd14: table_lookup = 32'h3EE00000;
                        4'd15: table_lookup = 32'h3EF00000;
                    endcase
                end
                3'd5: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3C800000;
                        4'd2:  table_lookup = 32'h3D000000;
                        4'd3:  table_lookup = 32'h3D400000;
                        4'd4:  table_lookup = 32'h3D800000;
                        4'd5:  table_lookup = 32'h3DA00000;
                        4'd6:  table_lookup = 32'h3DC00000;
                        4'd7:  table_lookup = 32'h3DE00000;
                        4'd8:  table_lookup = 32'h3E000000;
                        4'd9:  table_lookup = 32'h3E100000;
                        4'd10: table_lookup = 32'h3E200000;
                        4'd11: table_lookup = 32'h3E300000;
                        4'd12: table_lookup = 32'h3E400000;
                        4'd13: table_lookup = 32'h3E500000;
                        4'd14: table_lookup = 32'h3E600000;
                        4'd15: table_lookup = 32'h3E700000;
                    endcase
                end
                3'd6: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3C000000;
                        4'd2:  table_lookup = 32'h3C800000;
                        4'd3:  table_lookup = 32'h3CC00000;
                        4'd4:  table_lookup = 32'h3D000000;
                        4'd5:  table_lookup = 32'h3D200000;
                        4'd6:  table_lookup = 32'h3D400000;
                        4'd7:  table_lookup = 32'h3D600000;
                        4'd8:  table_lookup = 32'h3D800000;
                        4'd9:  table_lookup = 32'h3D900000;
                        4'd10: table_lookup = 32'h3DA00000;
                        4'd11: table_lookup = 32'h3DB00000;
                        4'd12: table_lookup = 32'h3DC00000;
                        4'd13: table_lookup = 32'h3DD00000;
                        4'd14: table_lookup = 32'h3DE00000;
                        4'd15: table_lookup = 32'h3DF00000;
                    endcase
                end
                3'd7: begin
                    unique case (pos)
                        4'd0:  table_lookup = 32'h00000000;
                        4'd1:  table_lookup = 32'h3B800000;
                        4'd2:  table_lookup = 32'h3C000000;
                        4'd3:  table_lookup = 32'h3C400000;
                        4'd4:  table_lookup = 32'h3C800000;
                        4'd5:  table_lookup = 32'h3CA00000;
                        4'd6:  table_lookup = 32'h3CC00000;
                        4'd7:  table_lookup = 32'h3CE00000;
                        4'd8:  table_lookup = 32'h3D000000;
                        4'd9:  table_lookup = 32'h3D100000;
                        4'd10: table_lookup = 32'h3D200000;
                        4'd11: table_lookup = 32'h3D300000;
                        4'd12: table_lookup = 32'h3D400000;
                        4'd13: table_lookup = 32'h3D500000;
                        4'd14: table_lookup = 32'h3D600000;
                        4'd15: table_lookup = 32'h3D700000;
                    endcase
                end
            endcase
        end
    endfunction

    logic [3:0] running_count;

    always_comb begin
        out_alibi_next = 512'b0;
        for (int j = 0; j < 16; j = j + 1) begin
            out_alibi_next[j*32 +: 32] = table_lookup(head_idx, position[j]);
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy      <= 1'b0;
            head_idx  <= 3'b000;
            for (int j = 0; j < 16; j = j + 1) begin
                position[j] <= 4'b0000;
            end
        end else if (input_accepted) begin
            busy          <= 1'b1;
            head_idx      <= 3'b000;
            running_count = 4'b0000;
            for (int j = 0; j < 16; j = j + 1) begin
                if (in_attention_mask[j]) begin
                    position[j] <= running_count;
                    running_count = running_count + 4'd1;
                end else begin
                    position[j] <= 4'b0000;
                end
            end
        end else if (output_consumed) begin
            if (head_idx == 3'd7) begin
                busy     <= 1'b0;
                head_idx <= 3'b000;
            end else begin
                busy     <= 1'b1;
                head_idx <= head_idx + 3'd1;
            end
        end
    end

endmodule
