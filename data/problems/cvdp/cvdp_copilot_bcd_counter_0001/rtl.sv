module bcd_counter (
    input logic clk, input logic rst,
    output logic [3:0] ms_hr, ls_hr, ms_min, ls_min, ms_sec, ls_sec
);
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            ms_hr <= 0; ls_hr <= 0; ms_min <= 0;
            ls_min <= 0; ms_sec <= 0; ls_sec <= 0;
        end else if (ls_sec != 9) ls_sec <= ls_sec + 1'b1;
        else begin
            ls_sec <= 0;
            if (ms_sec != 5) ms_sec <= ms_sec + 1'b1;
            else begin
                ms_sec <= 0;
                if (ls_min != 9) ls_min <= ls_min + 1'b1;
                else begin
                    ls_min <= 0;
                    if (ms_min != 5) ms_min <= ms_min + 1'b1;
                    else begin
                        ms_min <= 0;
                        if (ms_hr == 2 && ls_hr == 3) begin
                            ms_hr <= 0; ls_hr <= 0;
                        end else if (ls_hr == 9) begin
                            ls_hr <= 0; ms_hr <= ms_hr + 1'b1;
                        end else ls_hr <= ls_hr + 1'b1;
                    end
                end
            end
        end
    end
endmodule
